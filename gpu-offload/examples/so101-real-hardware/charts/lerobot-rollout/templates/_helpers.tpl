{{- define "lerobot-rollout.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "lerobot-rollout.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "lerobot-rollout.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "lerobot-rollout.labels" -}}
app.kubernetes.io/name: {{ include "lerobot-rollout.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}
