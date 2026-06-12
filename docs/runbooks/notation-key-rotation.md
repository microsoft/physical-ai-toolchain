# Notation AKV Key Rotation Runbook

**Cadence**: Quarterly (every 90 days), plus on-demand for suspected compromise.
**Audience**: Reference architecture operators using `signing_mode = "notation"`.
**Automation**: `.github/workflows/notation-key-rotate.yml` (cron `0 6 1 */3 *` and `workflow_dispatch`).

## Scope

This runbook applies only to the Notation v1 + Azure Key Vault HSM signing path. Sigstore cosign keyless signing rotates trust automatically through short-lived Fulcio certificates and does not require this procedure.

## When to Rotate

| Trigger                                             | Action                                                                                                    |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Quarterly cadence                                   | Automated workflow runs `0 6 1 */3 *` UTC. No operator action unless the workflow opens a tracking issue. |
| Workflow opens issue                                | Follow [Post-Rotation Verification](#post-rotation-verification).                                         |
| Suspected key compromise                            | Trigger workflow via `workflow_dispatch`; immediately follow [Compromise Response](#compromise-response). |
| AKV HSM key version reaches 1 year                  | Rotate even if quarterly cron has not fired yet.                                                          |
| Signer workflow's GitHub OIDC trust binding changes | Rotate to invalidate any prior signatures associated with the old binding.                                |

## Pre-Rotation Checklist

* [ ] Confirm the previous quarterly rotation issue is closed.
* [ ] Confirm `signing_mode = "notation"` is the active mode for at least one cluster.
* [ ] Confirm the AKV HSM key referenced by `notation_akv_key_id` is `Enabled`.
* [ ] Confirm Premium ACR (`should_enable_premium_acr = true`) is provisioned.
* [ ] Confirm at least one operator has `Key Vault Crypto Officer` on the AKV.

## Automated Rotation Procedure

The `notation-key-rotate.yml` workflow performs these steps without operator interaction:

1. Authenticates via the GitHub Actions OIDC federation provisioned by `infrastructure/terraform/modules/github-oidc/`.
2. Calls `az keyvault key rotate --vault-name "$NOTATION_AKV_NAME" --name "$NOTATION_AKV_KEY_NAME"`.
3. Reads the new key version with `az keyvault key show --query 'key.kid'`.
4. Opens a GitHub issue titled `Notation key rotated: <new-version>` containing the new `kid`, the rotation timestamp, and a checklist linking back to this runbook.
5. Surfaces the new `kid` as a workflow output for downstream consumption.

The workflow does not push the new key id into Terraform variables. That update is intentionally manual — see [Apply the New Key Version](#apply-the-new-key-version).

## Apply the New Key Version

After the rotation issue is opened, an operator with Terraform write access performs:

```bash
# 1. Read the new kid from the rotation issue.
NEW_KID="https://<vault>.vault.azure.net/keys/<key>/<new-version>"

# 2. Update the Terraform variable.
cd infrastructure/terraform
sed -i "s|notation_akv_key_id *= *\".*\"|notation_akv_key_id = \"${NEW_KID}\"|" terraform.tfvars

# 3. Plan and apply the change. The notation-akv module re-derives the trust
#    policy bundle distributed to clusters via Flux.
source prerequisites/az-sub-init.sh
terraform plan -var-file=terraform.tfvars -out=rotation.tfplan
terraform apply rotation.tfplan
```

## Re-Sign Active Image Tags

Existing image signatures remain valid against the previous key version until that version is disabled. To re-sign critical tags against the new key:

```bash
# Trigger the publish workflow against the latest released digest.
gh workflow run dataviewer-image-publish.yml \
  --ref main \
  --field image_tag=<latest-released-tag>
gh workflow run lerobot-eval-image-publish.yml \
  --ref main \
  --field image_tag=<latest-released-tag>
```

Each workflow re-signs against the new key version and pushes the new signature manifest to ACR.

## Post-Rotation Verification

Run the operator helper against a freshly re-signed image:

```bash
./scripts/security/verify-image.sh \
  --mode=notation \
  myacr.azurecr.io/dataviewer-backend@sha256:<digest>
```

Expected output: `Notation verification succeeded: cn=<signer>, akv-key-version=<new-version>`.

Verify Kyverno admission still admits the re-signed image:

```bash
./scripts/security/check-admission-readiness.sh --mode=notation
```

Expected output: `Notation trust policy bundle synced; admission ready.`

## Compromise Response

If the AKV HSM key is suspected to be compromised:

1. **Disable the current key version immediately**: `az keyvault key set-attributes --vault-name "$NOTATION_AKV_NAME" --name "$NOTATION_AKV_KEY_NAME" --enabled false`.
2. Trigger `notation-key-rotate.yml` via `workflow_dispatch`.
3. Follow [Apply the New Key Version](#apply-the-new-key-version).
4. Re-sign every active image tag (see [Re-Sign Active Image Tags](#re-sign-active-image-tags)) — not just the most recent.
5. Audit Rekor and ACR signature manifests for unauthorized signatures published before key disable. Notation signatures do not appear in Rekor; query ACR directly: `az acr manifest list-metadata --registry "$ACR_NAME" --name "<repo>" --orderby time_desc`.
6. Open an incident issue with the `security:incident` label and link the rotation issue.
7. Notify downstream consumers via the project's security mailing list (see `SECURITY.md`).

## Rollback

Rotation is non-reversible at the AKV layer (a rotated key generates a new HSM-bound version that cannot be replayed). To restore the previous trust state, set `notation_akv_key_id` back to the prior version's `kid`, run `terraform apply`, and re-sign critical tags against the prior key version. The prior version must still be `Enabled` in AKV — confirm before attempting rollback.

## References

* [Container Image Signing](../security/container-signing.md) — full architecture and Notation mode surface.
* [ADR: Container Signing — Public Rekor as Default](../adrs/container-signing-public-rekor.md) — decision record covering the Notation opt-out.
* [`infrastructure/terraform/modules/notation-akv/`](../../infrastructure/terraform/modules/notation-akv/) — Terraform module managing the AKV HSM key and federated identity.
* [`.github/workflows/notation-key-rotate.yml`](../../.github/workflows/notation-key-rotate.yml) — automation workflow.
* [Notation v1 Specification](https://github.com/notaryproject/specifications) — upstream signing format.
