# HyperAI Native Applications Specification

Source: https://ide-tutorial.hyperai.di.uoa.gr/dsl/native-apps/ (HyperAI IDE tutorial, captured 2026-10-06).

A native application is described by an application profile written in YAML.

## Profile Structure

Every application profile follows this YAML layout:

```yaml
applicationProfile:
  metadata:
    # application identity and annotations
  specs:
    # runtime, resources, network, constraints, qos
  status:
    # runtime state, updated dynamically
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `applicationProfile.metadata` | object | yes | Application metadata |
| `applicationProfile.specs` | object | yes | Application requirements |
| `applicationProfile.status` | object | no | Runtime state, updated dynamically |

## Field Reference

### `metadata` — Application Metadata

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `type` | string, `native` | yes | `"native"` | Application profile type: integration, native, or device. |
| `schemaVersion` | string | yes | `"1.1.0"` | App profile schema version using semantic versioning |
| `name` | string | yes | `"hello-world-webserver"` | Globally unique application name for identification. |
| `version` | string | yes | `"1.0.0"` | Internal version identifier for the application instance. |
| `description` | string | no | `"Nginx web server..."` | A short description of what the application does. |
| `owner` | string | yes | `"UoA-Team"` | Person or organization responsible for this application. |
| `lifecyclePhase` | string, enum: `development` `testing` `production` | yes | `"development"` | Lifecycle stage: development, testing, or production. |
| `createdAt` | string (RFC 3339) | no | `"2026-06-04T12:00:00Z"` | Creation timestamp in RFC 3339 format (UTC). |
| `updatedAt` | string (RFC 3339) | no | `"2026-06-04T12:30:00Z"` | Last modification timestamp in RFC 3339 format (UTC). |
| `annotations.intent` | string | no | `"platform-demo"` | Purpose of the application |
| `annotations.domain` | string | no | `"education"` | Classification of application (e.g., AI, Automotive, robotics) |
| `annotations.language` | string | no | `"Python 3.10"` | Programming language used by the application |
| `annotations.dependencies` | list | no | `["fastapi", "uvicorn"]` | Required software libraries (e.g., ML frameworks) |

### `specs.runtime` — Runtime Requirements

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `executionType` | string, enum: `container` `vm` | yes | `"container"` | Type of execution platform (e.g., container, vm, native, android) |
| `entryPoint` | string | yes | `"uvicorn"` | Path to the main script or command executed at container startup |
| `args` | list | yes | `["main:app", "--port", "8000"]` | Arguments passed to the entry point command |
| `baseOS.name` | string | yes | `"python"` | Name of base OS (e.g., ubuntu) |
| `baseOS.version` | string | yes | `"3.10-slim"` | Version of the base OS (e.g., 20.04) |
| `containerImage.uri` | string | yes | `"registry.example.com/org/app"` | OCI-compliant container image URI |
| `containerImage.tag` | string | yes | `"latest"` | Optional image tag (containers) |
| `hypervisor` | string, enum: `qemu` `kvm` `xen` `vmware` | no | `"kvm"` | Hypervisor/manager (qemu, kvm, xen, vmware) |
| `acceleratorRuntime[].name` | string | no | `"cuda"` | Name of the runtime environment for accelerators |
| `acceleratorRuntime[].version` | string | no | `"12.2"` | Version of the accelerator runtime |

### `specs.resources` — Resource Requirements

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `cpu` | string (>0, millicores) | yes | `"2000m"` | Number of CPU cores required (e.g., '1000m' for 1 core). |
| `memory` | string (`Mi` `Gi` `Ti`) | yes | `"10Gi"` | Memory requirement. |
| `storage` | string (`Mi` `Gi` `Ti`) | yes | `"1Gi"` | Storage requirement (if needed). |
| `gpu` | integer (>=0) | no | `1` | Required GPUs. |
| `tpu` | integer (>=0) | no | `0` | Required TPUs. |
| `accelerators` | integer (>=0) | no | `0` | List of FPGAs, ASICs |

### `specs.network` — Network Requirements

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `ports[].port` | integer | yes | `8000` | port number |
| `ports[].protocol` | string | yes | `"TCP"` | protocol used by this port |
| `ports[].publicExposure` | boolean | no | `true` | Whether the port is publicly exposed |
| `protocols` | list | no | `["HTTP"]` | Application-level protocols used by the application (e.g., HTTP, gRPC) |
| `networkBandwidthMin` | string (`Mbps` `Gbps`) | no | `"100Mbps"` | Minimum network bandwidth. |

### `specs.constraints` — Scheduling Constraints

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `supportedArchitectures` | list | yes | `["x86_64"]` | Compatible CPU/GPU architectures. |
| `trustScore` | string (1–5) | no | `"4.5"` | Reliability and security score. |
| `geoLocationRequirement` | string | no | `"Europe-only"` | Location restrictions. |
| `isHighlyAvailable` | boolean | no | `false` | Specifies if deployment requires redundancy. |
| `faultTolerance` | string | no | `"restart"` | Resiliency strategy (e.g., restart policy). |
| `securityLevel` | string (1–3) | no | `"high"` | Security classification (e.g., 3: high, 2: medium, 1: low). |
| `dataClassification` | string | no | `"private"` | Data sensitivity |

### `specs.qos` — Quality of Service

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `latencyToleranceMax` | string (ms) | no | `"150ms"` | Maximum tolerable latency. |
| `energyCost` | string (kWh) | no | `"0.5kWh"` | Estimated energy consumption per hour. |
| `monetaryCost` | string (currency) | no | `"0.05"` | Deployment cost per hour. |
| `resilience` | string | no | `"restart"` | Tolerance and failure mechanism (e.g., restart) |
| `availability` | string (%) | no | `"99.0%"` | Uptime SLA target |
| `startupTime` | string (secs) | no | `"10s"` | Time to become operational |

Note: `resilience` is redundant to `faultTolerance` and will be removed in the next schema version.

### `status` — Runtime Status (Dynamic)

This field reflects runtime state and is updated dynamically; it is not set in the profile.

| Field | Type | Required | Example | Description |
|-------|------|----------|---------|-------------|
| `isOnline` | boolean | yes | `true` | Whether the application is currently active |
| `lastHeartBeat` | string (RFC 3339) | yes | `"2026-06-04T12:30:00Z"` | Last time the app sent a status signal |
| `runtimeState` | string | yes | `"running"` | Current runtime status of app (e.g., running, failed) |
| `isStateless` | boolean | no | `false` | Indicates if the application maintains state. |
| `resourceUsage.cpu` | string | yes | `"2000m"` | CPU usage (e.g., 2000m) |
| `resourceUsage.memory` | string | yes | `"512Mi"` | RAM usage |
| `resourceUsage.storage` | string | yes | `"1Gi"` | Storage consumed |
| `resourceUsage.gpu` | integer (>=0) | no | `0` | Number of GPUs consumed |
| `resourceUsage.tpu` | integer (>=0) | no | `0` | Number of TPUs consumed |
| `resourceUsage.accelerators` | integer (>=0) | no | `0` | Number of FPGAs, ASICs used |
