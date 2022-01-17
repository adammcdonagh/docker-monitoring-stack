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

# Setup test checks 
if ./sensuctl check info check-disk-usage >/dev/null 2>&1; then
  ./sensuctl check delete check-disk-usage --skip-confirm
fi
./sensuctl check create check-disk-usage \
  --command 'check-disk-usage -w {{.labels.disk_warning | default 80}} -c {{.labels.disk_critical | default 90}}' \
  --interval 10 \
  --subscriptions unix \
  --runtime-assets sensu/check-disk-usage
