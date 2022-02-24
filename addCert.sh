#!/usr/bin/env bash -x

for container in `docker ps -q --no-trunc | head -1`; do
  docker exec -it $container "update-ca-certificates"
done


