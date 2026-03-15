# Network Topology

Virtual network architecture, private connectivity, and DNS configuration for the Physical AI Toolchain.

## Components

| Component | Purpose |
|-----------|---------|
| Virtual Network | Address space for all cluster and service subnets |
| AKS Subnet | Kubernetes node networking |
| Pod Subnet | AKS pod CIDR (Azure CNI Overlay) |
| Private Endpoint Subnet | Private link connections to PaaS services |
| DNS Resolver Subnet | Private DNS resolution for VPN clients |
| NAT Gateway | Deterministic outbound IP for allowlisting |
| VPN Gateway | Point-to-site VPN for private cluster access |
| Private DNS Zones | Name resolution for private endpoints |

## Configuration

### Subnet Layout

| Subnet | Default CIDR | Purpose |
|--------|-------------|---------|
| Default | `10.0.1.0/24` | General services |
| Private Endpoints | `10.0.2.0/24` | Private link connections |
| AKS Nodes | `10.0.5.0/24` | Kubernetes node IPs |
| AKS Pods | `10.0.6.0/24` | Pod overlay network |
| GPU Pool(s) | `10.0.7.0/24`+ | Per-pool subnets |
| DNS Resolver | `10.0.9.0/28` | Inbound DNS forwarding |

### Network Modes

| Mode | `should_enable_private_endpoint` | `should_enable_private_aks_cluster` | Access Pattern |
|------|----------------------------------|-------------------------------------|----------------|
| Full Private | `true` | `true` | VPN required for all access |
| Hybrid | `true` | `false` | Private data, public API server |
| Full Public | `false` | `false` | Direct access (evaluation only) |

### VPN Configuration

The VPN module deploys as a standalone Terraform root in `infrastructure/terraform/vpn/`. It uses `data` sources to discover the existing VNet and resource group rather than remote state references.

VPN supports point-to-site connections with certificate-based authentication.

### DNS Configuration

The DNS module deploys as a standalone Terraform root in `infrastructure/terraform/dns/`. Private DNS zones provide name resolution for:

- AKS API server (`privatelink.<region>.azmk8s.io`)
- Container Registry (`privatelink.azurecr.io`)
- Key Vault (`privatelink.vaultcore.azure.net`)
- Storage (`privatelink.blob.core.windows.net`)
- PostgreSQL (`privatelink.postgres.database.azure.com`)

## Dependencies

- Azure Infrastructure: resource group, location
- Kubernetes Setup: AKS cluster consumes VNet and subnets
- Identity and Access: VPN certificates stored in Key Vault
