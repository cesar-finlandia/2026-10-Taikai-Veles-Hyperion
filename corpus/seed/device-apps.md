# HyperAI IDE Device Apps Specification

Source: https://ide-tutorial.hyperai.di.uoa.gr/dsl/devices/ (HyperAI IDE tutorial, captured 2026-10-06).

A device application is deployed to a registered device (Android phone, ESP32 board, Docker-capable edge node).

## Manifest Structure

Every Application manifest follows this standard Kubernetes-style resource layout:

```yaml
apiVersion: hyper.ai/v1
kind: Application
metadata:
  name: my-app
  annotations:
    intent: "Computer vision capture"
spec:
  # the application definition
status:
  # read-only, set by the platform
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `apiVersion` | string | yes | `hyper.ai/v1` |
| `kind` | string | yes | `Application` |
| `metadata` | object | yes | Application metadata |
| `spec` | object | yes | Application definition |
| `status` | object | no | Set by platform (read-only) |

## Field Reference

### metadata — Application Metadata

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `name` | string | yes | `"vision-capture-android"` |
| `annotations` | map[string]string | no | `intent: "Computer vision capture"` |

### spec — Application Definition

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `device_name` | string | no | `"edge-device-01"` |
| `device_uuid` | string | no | `"40637429-b8ed-4817-b7fd-f32bc6834d9b"` |

### spec.app — Application Identity

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `type` | string, enum: `device` | yes | `device` |
| `schemaVersion` | string | yes | `"1.0.0"` |
| `name` | string | yes | `"vision-capture"` |
| `version` | string | yes | `"2.3.0"` |
| `owner` | string | yes | `"Edge Vision Team"` |
| `lifecyclePhase` | string, enum: `development`, `testing`, `production` | yes | `"production"` |
| `description` | string | no | `"Captures frames and emits detections."` |
| `parentApp.name` | string | no | `"parent-app"` |
| `parentApp.uid` | string | no | `"abc-123"` |

### spec.workload — Workload Definition

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `kind` | string, enum: `AndroidApk`, `DockerImage`, `esp32Binary` | yes | `"DockerImage"` |

#### AndroidApk

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `androidApk.apkUrl` | string (URI) | yes | `"https://example.com/app.apk"` |
| `androidApk.packageName` | string | yes | `"com.example.myapp"` |
| `androidApk.sha256` | string | no | `"a3f5c..."` |
| `androidApk.installMode` | string, enum: `install`, `update` | no | `"update"` |
| `androidApk.launch.activity` | string | no | `"com.example.myapp/.MainActivity"` |
| `androidApk.launch.action` | string | no | `"android.intent.action.MAIN"` |
| `androidApk.launch.category` | string | no | `"android.intent.category.LAUNCHER"` |

#### DockerImage

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `dockerImage.image` | string | yes | `"nginx:latest"` |
| `dockerImage.imagePullPolicy` | string, enum: `Always`, `IfNotPresent`, `Never` | no | `"IfNotPresent"` |
| `dockerImage.imagePullSecretRef` | string | no | `"registry-credentials"` |

#### esp32Binary

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `esp32Binary.binaryUrl` | string (URI) | yes | `"https://example.com/firmware.bin"` |
| `esp32Binary.chip` | string, enum: `esp32`, `esp32s2`, `esp32s3`, `esp32c3`, `esp32c6`, `esp32h2` | yes | `"esp32s3"` |
| `esp32Binary.flash.method` | string, enum: `serial`, `ota` | yes | `"ota"` |
| `esp32Binary.sha256` | string | no | `"b2f4a..."` |
| `esp32Binary.flash.port` | string | no | `"/dev/ttyUSB0"` |
| `esp32Binary.flash.baudRate` | integer (>=1200) | no | `115200` |
| `esp32Binary.flash.offset` | string (hex) | no | `"0x10000"` |
| `esp32Binary.flash.partition` | string | no | `"app0"` |
| `esp32Binary.flash.eraseFlash` | boolean | no | `true` |

### spec.exec — Execution Parameters

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `parameters` | map[string]string | no | `STREAM_URL: "rtsp://10.0.0.5/live"` |
| `command` | array of strings | no | `["/app/start", "--verbose"]` |
| `env` | map[string]string | no | `LOG_LEVEL: "info"` |
| `workingDir` | string | no | `"/app"` |

### spec.resources — Resource Requirements

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `cpu.value` | number | no | `250` |
| `cpu.unit` | string, enum: `cores`, `millicores` | no | `"millicores"` |
| `memory.value` | number | no | `256` |
| `memory.unit` | string, enum: `MiB`, `GiB`, etc. | no | `"MiB"` |
| `storage.value` | number | no | `1` |
| `storage.unit` | string, enum: `GiB`, etc. | no | `"GiB"` |
| `gpu` | integer (>=0) | no | `1` |
| `tpu` | integer (>=0) | no | `0` |
| `accelerators` | array of strings | no | `["cuda", "tensorrt"]` |

### spec.network — Network Requirements

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `ports[].port` | integer (1–65535) | yes | `8080` |
| `ports[].protocol` | string | yes | `"HTTP"` |
| `networkBandwidthMin.value` | number | yes | `10` |
| `networkBandwidthMin.unit` | string, enum: `bps`, `Kbps`, `Mbps`, `Gbps` | yes | `"Mbps"` |

### spec.qos — Quality of Service

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `latencyToleranceMax.value` | number | yes | `150` |
| `latencyToleranceMax.unit` | string, enum: `ms`, `s` | yes | `"ms"` |
| `energyCost.value` | number | yes | `5` |
| `energyCost.unit` | string, enum: `mW`, `W` | yes | `"W"` |
| `monetaryCost.value` | number | yes | `0.05` |
| `monetaryCost.currency` | string | yes | `"USD"` |
| `monetaryCost.per` | string, enum: `second`, `minute`, `hour`, `day` | yes | `"hour"` |
| `resilience` | string | yes | `"auto-restart"` |
| `availability.value` | number (0–1) | yes | `0.95` |
| `availability.unit` | string, enum: `fraction` | yes | `"fraction"` |
| `startupTime.value` | number | yes | `3` |
| `startupTime.unit` | string, enum: `ms`, `s` | yes | `"s"` |

### spec.constraints — Scheduling Constraints

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `schedulingPriority` | integer | yes | `5` |
| `supportedArchitectures` | array of strings | yes | `["arm64-v8a", "amd64"]` |
| `geoLocationRequirement` | string | yes | `"LocalZone"` |
| `isHighlyAvailable` | boolean | yes | `false` |
| `faultTolerance` | string | yes | `"graceful-degradation"` |
| `dataClassification` | string | yes | `"private"` |
| `trustScore` | number (>=0) | no | `90.0` |
| `batteryLevelMin` | integer (0–100) | no | `20` |
| `securityLevel` | string | no | `"high"` |

### spec.sensors — Sensor Requirements

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `sensorType` | string | yes | `"temperature"` |
| `isActive` | boolean | yes | `true` |
| `taskStatus` | string, enum: `IDLE`, `RUNNING`, `SCHEDULED` | yes | `"IDLE"` |
| `sensorIDs` | array of strings | no | `["sensor-001"]` |
| `platformInfo` | string | no | `"i2c-bus-1"` |

### spec.runtime — Runtime Requirements

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `baseOS.name` | string | no | `"Ubuntu"` |
| `baseOS.version` | string | no | `"22.04"` |
| `acceleratorRuntime[].name` | string | no | `"cuda"` |
| `hypervisor` | string | no | `"kvm"` |

### spec — Miscellaneous

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `logs_url` | string (URI) | no | `"https://logs.example.com/my-app"` |
| `metrics_url` | string (URI) | no | `"https://metrics.example.com/my-app"` |
| `config` | free-form object | no | See Custom Configuration Fields section |

### status — Deployment Status (Read-only)

| Phase | Meaning |
|-------|---------|
| `pending` | Deployment requested, waiting for device acknowledgment |
| `scheduled` | Device selected, workload being transmitted |
| `deployed` | Device acknowledged successful deployment |
| `failed` | Deployment failed or timed out |
| `removed` | Application deleted and device acknowledged removal |

## Custom Configuration Fields

The `spec.config` field allows any key-value structure for application-specific settings:

```yaml
spec:
  config:
    mqtt_broker: "mqtt://edge-broker:1883"
    sampling_rate: "100ms"
    model_path: "/models/person-v3.onnx"
    debug: "true"
```

```yaml
spec:
  config:
    camera_index: "0"
    resolution: "1920x1080"
    output_topic: "detections/camera-0"
```
