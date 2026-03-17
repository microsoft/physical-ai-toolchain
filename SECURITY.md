# Security

<!-- BEGIN MICROSOFT SECURITY.MD V0.0.9 BLOCK -->

Microsoft takes the security of our software products and services seriously, which includes all source code repositories managed through our GitHub organizations, which include [Microsoft](https://github.com/Microsoft), [Azure](https://github.com/Azure), [DotNet](https://github.com/dotnet), [AspNet](https://github.com/aspnet) and [Xamarin](https://github.com/xamarin).

If you believe you have found a security vulnerability in any Microsoft-owned repository that meets [Microsoft's definition of a security vulnerability](https://aka.ms/security.md/definition), please report it to us as described below.

## Reporting Security Issues

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them to the Microsoft Security Response Center (MSRC) at [https://msrc.microsoft.com/create-report](https://aka.ms/security.md/msrc/create-report).

If you prefer to submit without logging in, send email to [secure@microsoft.com](mailto:secure@microsoft.com). If possible, encrypt your message with our PGP key; please download it from the [Microsoft Security Response Center PGP Key page](https://aka.ms/security.md/msrc/pgp).

You should receive a response within 24 hours. If for some reason you do not, please follow up via email to ensure we received your original message. Additional information can be found at [microsoft.com/msrc](https://www.microsoft.com/msrc).

Please include the requested information listed below (as much as you can provide) to help us better understand the nature and scope of the possible issue:

- Type of issue (e.g. buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit the issue

This information will help us triage your report more quickly.

If you are reporting for a bug bounty, more complete reports can contribute to a higher bounty award. Please visit our [Microsoft Bug Bounty Program](https://aka.ms/security.md/msrc/bounty) page for more details about our active programs.

## Preferred Languages

We prefer all communications to be in English.

## Policy

Microsoft follows the principle of [Coordinated Vulnerability Disclosure](https://aka.ms/security.md/cvd).

<!-- END MICROSOFT SECURITY.MD BLOCK -->

## Vulnerability Remediation

The project maintainers commit to remediating confirmed vulnerabilities based on severity:

| Severity          | Remediation Target |
|-------------------|--------------------|
| Critical and High | 60 days            |
| Medium            | 90 days            |

Remediation timelines begin when the vulnerability is confirmed and may involve a code fix, configuration change, dependency update, or documented mitigation. Tracking is done through GitHub Security Advisories or GitHub issues. If a fix requires more time, the maintainers will publish a mitigation or workaround within the target window and document the extended timeline.

## Security Considerations for Deployers

> [!IMPORTANT]
> This reference architecture is provided under the [MIT License](LICENSE) and offered
> "AS IS" without warranty of any kind. The security guidance in this document is
> informational only and does not constitute professional security advice. You are
> solely responsible for evaluating the security of your own deployment, including
> all configuration, operational practices, and compliance requirements. The project
> maintainers accept no liability for security incidents arising from the use of
> this architecture.

This reference architecture includes certain security configurations as a starting
point. These configurations are not a substitute for a security assessment tailored
to your environment.

### What This Architecture Includes

| Included Configuration      | Description                                   | Your Responsibility                                                 |
|-----------------------------|-----------------------------------------------|---------------------------------------------------------------------|
| Private AKS cluster option  | Enabled by default via Terraform variable     | Evaluate whether private mode meets your network requirements       |
| Managed identities          | User-assigned identity for AKS                | Review identity permissions and scope for your workloads            |
| Azure Key Vault integration | Key Vault CSI driver configured               | Manage secret lifecycle, rotation, and access policies              |
| Kubernetes network policies | Azure CNI with network policy support enabled | Define and maintain policies appropriate for your workloads         |
| Workload identity           | Federated credential configuration for OSMO   | Verify audience restrictions and token scoping for your environment |

### What This Architecture Does Not Include

| Not Included                | Why                                                                | Where to Start                                                                                                                  |
|-----------------------------|--------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| Production hardening        | Requirements vary by organization and compliance framework         | [AKS baseline architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks) |
| Compliance certification    | Compliance is organization-specific and requires formal assessment | [Azure compliance documentation](https://learn.microsoft.com/azure/compliance/)                                                 |
| Web Application Firewall    | WAF configuration depends on ingress patterns and threat model     | [Azure WAF documentation](https://learn.microsoft.com/azure/web-application-firewall/)                                          |
| DDoS protection             | DDoS requirements depend on public exposure and SLA needs          | [Azure DDoS Protection](https://learn.microsoft.com/azure/ddos-protection/)                                                     |
| Penetration testing         | Pen testing is an operational responsibility                       | [Azure penetration testing rules](https://learn.microsoft.com/azure/security/fundamentals/pen-testing)                          |
| Remote Terraform state      | State backend choice depends on team size and workflow             | [Terraform Azure backend](https://developer.hashicorp.com/terraform/language/backend/azurerm)                                   |
| Secret rotation automation  | Rotation schedules are organization-specific                       | [Key Vault rotation](https://learn.microsoft.com/azure/key-vault/keys/how-to-configure-key-rotation)                            |
| Audit logging configuration | Logging requirements vary by compliance framework                  | [Azure Monitor](https://learn.microsoft.com/azure/azure-monitor/)                                                               |

### Your Responsibilities

You are responsible for all security decisions in your deployment:

- Conduct a security assessment appropriate for your environment and compliance requirements
- Review and customize all Terraform variables, network configurations, and RBAC assignments
- Implement production hardening controls beyond what this reference architecture includes
- Manage secrets, credentials, and access policies throughout the deployment lifecycle
- Monitor your deployment for security events and respond to incidents
- Keep dependencies and base images updated with security patches

Refer to official [Azure security documentation](https://learn.microsoft.com/azure/security/) for authoritative, current guidance.

## Security Documentation

For comprehensive security documentation including threat models and deployment security guidance, see [Security Documentation](docs/security/README.md).

## Additional Resources

<!-- cspell:words deployers -->

- [Azure security documentation](https://learn.microsoft.com/azure/security/) - authoritative security guidance for Azure services
- [AKS baseline architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks) - production-ready AKS security patterns
