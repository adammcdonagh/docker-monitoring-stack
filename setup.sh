#!/usr/bin/env bash

. ./secrets

if [ ! -e "sensuctl" ]; then
  # Download the latest release
  RELEASE_TAR=sensu-go_6.6.3_darwin_amd64.tar.gz
  curl -LO https://s3-us-west-2.amazonaws.com/sensu.io/sensu-go/6.6.3/${RELEASE_TAR}

  # Extract the archive
  mkdir sensu-binary
  cd sensu-binary
  tar -xvf ../${RELEASE_TAR}
  mv sensuctl ../
  cd ..

  # Copy the executable into your PATH
  rm -r ${RELEASE_TAR} ./sensu-binary
fi

PATH=$PATH:`pwd`
sensuctl configure --username admin --password password -n --url http://localhost:8080

# Setup DB
sensuctl create --file postgres.yml

# Add assets
sensuctl asset add sensu/check-disk-usage
sensuctl asset add sensu/system-check:0.1.1
sensuctl asset add sensu/sensu-sumologic-handler

# Setup test checks 
for check_name in check-disk-usage metrics-proxy-system-check metrics-system; do

  if sensuctl check info ${check_name} >/dev/null 2>&1; then
    sensuctl check delete ${check_name} --skip-confirm
  fi
done


# Create 20 proxy agents
# for i in `seq 1 20`; do
#   ./sensuctl entity delete agent-proxy-${i} --skip-confirm 2>/dev/null 1>&2
#   # ./sensuctl entity create -c proxy agent-proxy-${i} -s unix
# done

cat << EOF | sensuctl create
---
type: SumoLogicMetricsHandler
api_version: pipeline/v1
metadata:
  name: sumologic_metrics
spec:
  url: "${SUMO_COLLECTION_URL}"
  max_connections: 10
  timeout: 10s
EOF

cat << EOF | sensuctl create
---
type: Pipeline
api_version: core/v2
metadata:
  name: sensu_metrics_to_sumo
spec:
  workflows:
  - name: metrics_to_sumologic
    filters:
    - name: has_metrics
      type: EventFilter
      api_version: core/v2
    handler:
      name: sumologic_metrics
      type: SumoLogicMetricsHandler
      api_version: pipeline/v1
EOF

cat << EOF | sensuctl create
---
type: EventFilter
api_version: core/v2
metadata:
  name: state_change_only
spec:
  action: allow
  expressions:
  - event.check.occurrences == 1
  runtime_assets: []
EOF

cat << EOF | sensuctl create
---
type: Pipeline
api_version: core/v2
metadata:
  name: sensu_checks_to_sumo
spec:
  workflows:
  - name: checks_to_sumologic
    filters:
    - name: state_change_only
      type: EventFilter
      api_version: core/v2
    handler:
      name: checks-to-sumologic
      type: Handler
      api_version: core/v2
EOF

# Set up the Sumo Logic event handler
cat << EOF | sensuctl create
type: Handler
api_version: core/v2
metadata:
  name: checks-to-sumologic
spec:
  type: pipe
  command: >-
    sensu-sumologic-handler
    --send-log
    --source-host "{{ .Entity.Name }}"
    --source-name "{{ .Check.Name }}"
    --url "${SUMO_COLLECTION_URL}"
  runtime_assets:
  - sensu/sensu-sumologic-handler
  timeout: 10
  filters:
  - is_incident
EOF

sensuctl handler create event-storage --type set --handlers sensu_checks_to_sumo
sensuctl handler create metrics-storage --type set --handlers sensu_metrics_to_sumo

# # Disk usage check
# ./sensuctl check create check-disk-usage \
#   --command 'check-disk-usage -w {{.labels.disk_warning | default 80}} -c {{.labels.disk_critical | default 90}}' \
#   --interval 10 \
#   --subscriptions unix \
#   --runtime-assets sensu/check-disk-usage

# # Host metrics
# ./sensuctl check create metrics-system \
#   --command 'system-check' \
#   --interval 10 \
#   --timeout 5 \
#   --subscriptions unix \
#   --runtime-assets sensu/system-check

cat << EOF | sensuctl create
---
type: CheckConfig
api_version: core/v2
metadata:
  name: check-disk-usage
spec:
  command: "check-disk-usage -w {{.labels.disk_warning | default 8}} -c {{.labels.disk_critical | default 90}}"
  runtime_assets:
  - sensu/check-disk-usage
  subscriptions:
  - unix
  interval: 10
  timeout: 5
  publish: true
  handlers:
  - event-storage
EOF

# Proxy check
cat <<EOF | ./sensuctl create
---
type: CheckConfig
api_version: core/v2
metadata:
  name: metrics-proxy-system-check
spec:
  command: system-check
  interval: 10
  proxy_requests:
    entity_attributes:
    - entity.entity_class == 'proxy'
  publish: true
  round_robin: true
  runtime_assets:
    - sensu/system-check
  subscriptions:
  - proxy
  pipelines:
  - type: Pipeline
    api_version: core/v2
    name: sensu_metrics_to_sumo
  output_metric_format: prometheus_text
  output_metric_tags:
  - name: entity
    value: "{{ .name }}"
  - name: namespace
    value: "{{ .namespace }}"
  - name: os
    value: "{{ .system.os }}"
  - name: platform
    value: "{{ .system.platform }}"
EOF

cat << EOF | sensuctl create
---
type: CheckConfig
api_version: core/v2
metadata:
  name: metrics-system-check
spec:
  command: system-check
  runtime_assets:
  - sensu/system-check
  subscriptions:
  - unix
  interval: 10
  timeout: 5
  publish: true
  pipelines:
  - type: Pipeline
    api_version: core/v2
    name: sensu_metrics_to_sumo
  output_metric_format: prometheus_text
  output_metric_tags:
  - name: entity
    value: "{{ .name }}"
  - name: namespace
    value: "{{ .namespace }}"
  - name: os
    value: "{{ .system.os }}"
  - name: platform
    value: "{{ .system.platform }}"
EOF