#! /bin/bash

set -e
set -u
set -x

# install docker

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
sudo add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
   $(lsb_release -cs) \
   stable"

sudo apt-get update
sudo apt-get install docker-ce

mkdir -p traefik_config

echo '
logLevel = "DEBUG"

[web]
address = ":8080"

# Enable file configuration backend
[file]
filename = "/etc/traefik/rules.toml"
watch = true

[traefikLog]
logLevel = "DEBUG"

[accessLog]

' > traefik_config/traefik.toml

echo '
[backends]
  [backends.backend1]
    [backends.backend1.servers.server1]
    url = "http://localhost:8001"
  [backends.backend2]
    [backends.backend2.servers.server1]
    url = "http://localhost:8002"

[frontends]
  [frontends.frontend1]
  backend = "backend1"
    [frontends.frontend1.routes.test_1]
    rule = "Host: cluster1.testpachdash.gabrielgrant.ca"
  [frontends.frontend2]
  backend = "backend2"
    [frontends.frontend2.routes.test_1]
    rule = "Host: cluster2.testpachdash.gabrielgrant.ca"

' > /dev/null

echo '
[backends]
[frontends]
' > traefik_config/rules.toml

chmod ugo+rw traefik_config/traefik.toml
chmod ugo+rw traefik_config/rules.toml

#docker run -d -p 8080:8080 -p 80:80 -v $PWD/traefik.toml:/etc/traefik/traefik.toml -v $PWD/rules.toml:/etc/traefik/rules.toml traefik
docker run -d -p 8080:8080 -p 80:80 -v $PWD/traefik_config:/etc/traefik traefik

# create proxbox user
useradd proxbox
PROXBOX_SSH_DIR=/home/proxbox/.ssh
PROXBOX_AUTHORIZED_KEYS=$PROXBOX_SSH_DIR/authorized_keys
mkdir -p $PROXBOX_SSH_DIR
touch $PROXBOX_AUTHORIZED_KEYS
chown proxbox:proxbox $PROXBOX_AUTHORIZED_KEYS
chmod 600 $PROXBOX_AUTHORIZED_KEYS

mkdir pubkeys

echo '
# Allow binding to non-loppback interfaces
GatewayPorts yes
' >> /etc/ssh/sshd_config

sudo service restart ssh

#docker run -d -p 8000:8000 -v $PWD/pubkeys:/usr/src/app/pubkeys -v $PWD/rules.toml:/usr/src/app/rules.toml -v $PROXBOX_SSH_DIR:/root/.ssh gabrielgrant/proxbox-server:0.1.5

docker run -d -p 8000:8000 -v $PWD/pubkeys:/usr/src/app/pubkeys -v $PWD/rules.toml:/usr/src/app/rules.toml -v $PROXBOX_SSH_DIR:/usr/src/app/host_ssh gabrielgrant/proxbox-server:0.1.5

