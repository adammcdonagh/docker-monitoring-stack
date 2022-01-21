#!/usr/bin/env bash

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

# Setup DB
./sensuctl create --file postgres.yml

# Add assets
./sensuctl asset add sensu/check-disk-usage
./sensuctl asset add sensu/system-check:0.1.1

# Setup test checks 
for check_name in check-disk-usage metrics-proxy-system-check metrics-system; do

  if ./sensuctl check info ${check_name} >/dev/null 2>&1; then
    ./sensuctl check delete ${check_name} --skip-confirm
  fi
done

# Disk usage check
./sensuctl check create check-disk-usage \
  --command 'check-disk-usage -w {{.labels.disk_warning | default 80}} -c {{.labels.disk_critical | default 90}}' \
  --interval 10 \
  --subscriptions unix \
  --runtime-assets sensu/check-disk-usage

# Host metrics
./sensuctl check create metrics-system \
  --command 'system-check' \
  --interval 10 \
  --timeout 5 \
  --subscriptions unix \
  --runtime-assets sensu/system-check


# Create 20 proxy agents
for i in `seq 1 20`; do
  ./sensuctl entity delete agent-proxy-${i} --skip-confirm 2>/dev/null 1>&2
  ./sensuctl entity create -c proxy agent-proxy-${i} -s unix
done


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