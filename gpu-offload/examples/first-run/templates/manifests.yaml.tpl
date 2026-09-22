{{- $registry := trimSuffix "/" .Values.image.registry -}}
{{- $repository := .Values.image.repository -}}
{{- if $registry -}}
{{- $repository = printf "%s/%s" $registry $repository -}}
{{- end -}}
{{- $image := printf "%s:%s" $repository .Values.image.tag -}}
{{- if .Values.image.digest -}}
{{- $image = printf "%s@%s" $repository .Values.image.digest -}}
{{- end -}}
{{- $serverRepository := .Values.image.repository -}}
{{- if .Values.serverStage.image.repository -}}
{{- $serverRepository = .Values.serverStage.image.repository -}}
{{- end -}}
{{- if $registry -}}
{{- $serverRepository = printf "%s/%s" $registry $serverRepository -}}
{{- end -}}
{{- $serverTag := .Values.image.tag -}}
{{- if .Values.serverStage.image.tag -}}
{{- $serverTag = .Values.serverStage.image.tag -}}
{{- end -}}
{{- $serverImage := printf "%s:%s" $serverRepository $serverTag -}}
{{- $gpu := .Values.serverStage.gpu -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: gpu-offload-runtime
  namespace: {{ .Release.Namespace }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{ toYaml . | indent 2 }}
{{- end }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: gpu-offload-runtime
  namespace: {{ .Release.Namespace }}
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: gpu-offload-runtime
  namespace: {{ .Release.Namespace }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: gpu-offload-runtime
subjects:
  - kind: ServiceAccount
    name: gpu-offload-runtime
    namespace: {{ .Release.Namespace }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: first-run-offload
  namespace: {{ .Release.Namespace }}
data:
  remote.yaml: |
    serverstages:
      - name: {{ .Values.serverStage.name }}
        perclient: false
        serverimage: {{ $serverImage | quote }}
{{- if and $gpu.enabled (eq $gpu.platform "wsl-nvidia") }}
        env:
          - name: LD_LIBRARY_PATH
            value: {{ $gpu.driverLibraryPath | quote }}
{{- end }}
        resources:
          requests:
{{ toYaml .Values.serverStage.resources.requests | indent 12 }}
          limits:
{{- range $key, $value := .Values.serverStage.resources.limits }}
            {{ $key }}: {{ $value | quote }}
{{- end }}
{{- if $gpu.enabled }}
            {{ $gpu.resourceName }}: {{ $gpu.quantity | quote }}
{{- end }}
    remotefuncs:
      - "demo_model//predict":
          remoteloc: {{ .Values.serverStage.name }}
{{- if $gpu.enabled }}
      - "gpu_model//gpu_inference":
          remoteloc: {{ .Values.serverStage.name }}
{{- end }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: first-run-client
  namespace: {{ .Release.Namespace }}
  labels:
    app: first-run-client
    xavier: "true"
  annotations:
    xavierconfig: |
      remoteablecm: first-run-offload
      remoteableconts:
        - client
spec:
  replicas: 1
  selector:
    matchLabels:
      app: first-run-client
  template:
    metadata:
      labels:
        app: first-run-client
        xavier: "true"
    spec:
      serviceAccountName: gpu-offload-runtime
      containers:
        - name: client
          image: {{ $image | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          env:
            - name: REMOTERPORT
              value: "30001"
            - name: GPU_CHECK
              value: {{ $gpu.enabled | quote }}
          resources:
{{ toYaml .Values.clientResources | indent 12 }}
