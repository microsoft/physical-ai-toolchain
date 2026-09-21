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
{{- $modelPath := required "model.hostPath must point at the pi05 checkpoint directory on the node" .Values.model.hostPath -}}
{{- $cachePath := required "huggingFaceCache.hostPath must point at the HuggingFace cache on the node" .Values.huggingFaceCache.hostPath -}}
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
# The runtime discovers its server stage by listing pods carrying the apprmt label
# the controller assigns.
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
# The checkpoint stays on the node. A hostPath PersistentVolume is the only way to
# expose it to the generated server pod: controller/mutate.py copies configMap,
# downwardAPI, emptyDir, ephemeral, persistentVolumeClaim, projected, and secret
# volumes from the client, and rejects raw hostPath volumes.
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {{ .Release.Name }}-model
  labels:
    app: {{ .Release.Name }}
spec:
  capacity:
    storage: {{ .Values.model.capacity }}
  accessModes:
    - ReadOnlyMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  claimRef:
    name: {{ .Release.Name }}-model
    namespace: {{ .Release.Namespace }}
  hostPath:
    path: {{ $modelPath | quote }}
    type: Directory
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ .Release.Name }}-model
  namespace: {{ .Release.Namespace }}
spec:
  accessModes:
    - ReadOnlyMany
  storageClassName: ""
  volumeName: {{ .Release.Name }}-model
  resources:
    requests:
      storage: {{ .Values.model.capacity }}
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {{ .Release.Name }}-hf-cache
  labels:
    app: {{ .Release.Name }}
spec:
  capacity:
    storage: {{ .Values.huggingFaceCache.capacity }}
  accessModes:
    - ReadOnlyMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  claimRef:
    name: {{ .Release.Name }}-hf-cache
    namespace: {{ .Release.Namespace }}
  hostPath:
    path: {{ $cachePath | quote }}
    type: Directory
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ .Release.Name }}-hf-cache
  namespace: {{ .Release.Namespace }}
spec:
  accessModes:
    - ReadOnlyMany
  storageClassName: ""
  volumeName: {{ .Release.Name }}-hf-cache
  resources:
    requests:
      storage: {{ .Values.huggingFaceCache.capacity }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-offload
  namespace: {{ .Release.Namespace }}
data:
  # Keep in sync with ../remote.yaml.
  remote.yaml: |
    serverstages:
      - name: {{ .Values.serverStage.name }}
        perclient: false
        serverimage: {{ $serverImage | quote }}
        resources:
          requests:
{{ toYaml .Values.serverStage.resources.requests | indent 12 }}
          limits:
{{- range $key, $value := .Values.serverStage.resources.limits }}
            {{ $key }}: {{ $value | quote }}
{{- end }}
            {{ .Values.serverStage.gpu.resourceName }}: {{ .Values.serverStage.gpu.quantity | quote }}
    remoteclasses:
      # Remoting the concrete lerobot classes keeps the loaded policy and both
      # processor pipelines resident on the stage; the client holds only proxies.
      - "lerobot.policies.pi05.modeling_pi05/PI05Policy":
          remoteloc: {{ .Values.serverStage.name }}
      - "lerobot.processor.pipeline/DataProcessorPipeline":
          remoteloc: {{ .Values.serverStage.name }}
    remotefuncs:
      # singleinstance means "call once on the stage, then memoize the result", so
      # it belongs only on the calls whose result is the loaded policy itself.
      # reset and get_action must execute on every call.
      - "ur10e_offload/PolicyRunner/load":
          singleinstance: true
          remoteloc: {{ .Values.serverStage.name }}
      - "ur10e_offload/PolicyRunner/describe":
          singleinstance: true
          remoteloc: {{ .Values.serverStage.name }}
      - "ur10e_offload/PolicyRunner/reset":
          remoteloc: {{ .Values.serverStage.name }}
      - "ur10e_offload/PolicyRunner/get_action":
          remoteloc: {{ .Values.serverStage.name }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-control
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Release.Name }}-control
    xavier: "true"
  # The two opt-in signals the mutating controller looks for. Everything the
  # workload needs beyond this - the remote.yaml mount, REMOTER_CONFIG,
  # CONFIGFROMKUBE, SERVERLABEL, and the generated GPU server Deployment - is
  # injected at admission.
  annotations:
    xavierconfig: |
      remoteablecm: {{ .Release.Name }}-offload
      remoteableconts:
        - control
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{ .Release.Name }}-control
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}-control
        xavier: "true"
    spec:
      serviceAccountName: gpu-offload-runtime
{{- with .Values.nodeSelector }}
      nodeSelector:
{{ toYaml . | indent 8 }}
{{- end }}
      volumes:
        - name: ur10e-model
          persistentVolumeClaim:
            claimName: {{ .Release.Name }}-model
            readOnly: true
        - name: ur10e-hf-cache
          persistentVolumeClaim:
            claimName: {{ .Release.Name }}-hf-cache
            readOnly: true
{{- if .Values.robot.usb.enabled }}
        # hostPath is legal on the client. The controller only rejects it when
        # copying volumes into the generated server pod, which never sees USB.
        - name: ur10e-usb
          hostPath:
            path: {{ .Values.robot.usb.hostPath | quote }}
            type: Directory
{{- end }}
      containers:
        - name: control
          image: {{ $image | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          # The control container never reads either volume. It declares the mounts
          # so the controller propagates them to the generated server pod, which is
          # where the weights and the tokenizer are loaded.
          volumeMounts:
            - name: ur10e-model
              mountPath: {{ .Values.model.mountPath }}
              readOnly: true
            - name: ur10e-hf-cache
              mountPath: {{ .Values.huggingFaceCache.mountPath }}
              readOnly: true
{{- if .Values.robot.usb.enabled }}
            - name: ur10e-usb
              mountPath: {{ .Values.robot.usb.hostPath }}
{{- end }}
{{- if and .Values.robot.usb.enabled .Values.robot.usb.privileged }}
          securityContext:
            privileged: true
{{- end }}
          env:
            - name: REMOTERPORT
              value: "30001"
            # Env declared here is merged into the generated server container, so the
            # GPU stage resolves the same checkpoint path and tokenizer cache.
            - name: UR10E_MODE
              value: {{ .Values.policy.mode | quote }}
            - name: UR10E_CHECKPOINT_PATH
              value: {{ .Values.policy.checkpointMountPath | quote }}
            - name: UR10E_DEVICE
              value: {{ .Values.policy.device | quote }}
            - name: UR10E_TASK
              value: {{ .Values.policy.task | quote }}
            - name: UR10E_FPS
              value: {{ .Values.policy.fps | quote }}
            - name: UR10E_N_ACTION_STEPS
              value: {{ .Values.policy.nActionSteps | quote }}
            - name: UR10E_SELF_CHECK_STEPS
              value: {{ .Values.policy.selfCheckSteps | quote }}
            - name: UR10E_ROBOT_CONFIG
              value: {{ .Values.robot.configPath | quote }}
            - name: UR10E_DATASET_REPO_ID
              value: {{ .Values.robot.datasetRepoId | quote }}
            - name: UR10E_NUM_EPISODES
              value: {{ .Values.robot.numEpisodes | quote }}
            - name: UR10E_EPISODE_TIME_S
              value: {{ .Values.robot.episodeTimeS | quote }}
            - name: UR10E_STATE_DIM
              value: {{ .Values.robot.stateDim | quote }}
            - name: UR10E_MAX_STEPS
              value: {{ .Values.robot.maxSteps | quote }}
            - name: UR10E_HOME_SPEED
              value: {{ .Values.robot.homeSpeed | quote }}
            - name: UR10E_LOG_EVERY
              value: {{ .Values.robot.logEvery | quote }}
            - name: UR10E_DEBUG_INFERENCE
              value: {{ .Values.robot.debugInference | quote }}
            - name: HF_HOME
              value: {{ .Values.huggingFaceCache.mountPath | quote }}
            - name: HF_HUB_OFFLINE
              value: "1"
          resources:
{{ toYaml .Values.clientResources | indent 12 }}
