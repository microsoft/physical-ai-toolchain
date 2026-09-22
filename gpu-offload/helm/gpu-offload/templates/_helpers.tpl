{{/*
SPDX-License-Identifier: MIT
Adapted from an upstream GPU-offloading reference implementation.
*/}}

{{/*
Expand the chart name.
*/}}
{{- define "gpu-offload.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name.
*/}}
{{- define "gpu-offload.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version label value.
*/}}
{{- define "gpu-offload.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "gpu-offload.labels" -}}
helm.sh/chart: {{ include "gpu-offload.chart" . }}
{{ include "gpu-offload.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end -}}

{{/*
Selector labels.
*/}}
{{- define "gpu-offload.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gpu-offload.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Render a fully qualified image reference from a component's image config.
Prefers an immutable digest pin; falls back to a mutable tag; omits both when
neither is set (runtime resolves to the registry default, e.g. :latest).
Usage: include "gpu-offload.image" (dict "registry" .Values.image.registry "image" .Values.mutate.image)
*/}}
{{- define "gpu-offload.image" -}}
{{- $registry := .registry -}}
{{- $image := .image -}}
{{- $hasRegistry := and $registry (ne $registry "") -}}
{{- if $image.digest -}}
	{{- if $hasRegistry -}}
		{{- printf "%s/%s@%s" $registry $image.repository $image.digest -}}
	{{- else -}}
		{{- printf "%s@%s" $image.repository $image.digest -}}
	{{- end -}}
{{- else if $image.tag -}}
	{{- if $hasRegistry -}}
		{{- printf "%s/%s:%s" $registry $image.repository $image.tag -}}
	{{- else -}}
		{{- printf "%s:%s" $image.repository $image.tag -}}
	{{- end -}}
{{- else -}}
	{{- if $hasRegistry -}}
		{{- printf "%s/%s" $registry $image.repository -}}
	{{- else -}}
		{{- printf "%s" $image.repository -}}
	{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Mutate webhook TLS secret name.
Uses the supplied secret when present, otherwise falls back to the chart-managed
secret name used by cert-manager and the built-in generated TLS flow.
*/}}
{{- define "gpu-offload.mutate.tlsSecretName" -}}
{{- if .Values.mutate.tls.secretName -}}
{{- .Values.mutate.tls.secretName -}}
{{- else -}}
{{- printf "%s-mutate-tls" (include "gpu-offload.fullname" .) -}}
{{- end -}}
{{- end -}}
