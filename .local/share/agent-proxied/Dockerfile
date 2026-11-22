FROM ubuntu:24.04 AS base
LABEL org.agent-proxied.resource-check="1"
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates findutils locales procps python3 socat tzdata \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen && locale-gen \
    && LC_ALL=en_US.UTF-8 python3 -c 'import locale; locale.setlocale(locale.LC_ALL, "")' \
    && rm -rf /var/lib/apt/lists/*
COPY entrypoint.py /usr/local/lib/agent-entrypoint.py
RUN mkdir -p /home/agent /workspace /run/relay /run/wayland && chmod 700 /run/wayland
ENV HOME=/home/agent
ENTRYPOINT ["python3", "-I", "/usr/local/lib/agent-entrypoint.py"]

FROM base AS agents
RUN apt-get update && apt-get install -y --no-install-recommends \
        bubblewrap curl debianutils diffutils git gzip libatomic1 libstdc++6 ripgrep tar unzip \
    && rm -rf /var/lib/apt/lists/*
COPY codex-release/ /opt/codex/
COPY claude /usr/local/bin/claude
ENV PATH=/opt/codex/bin:/opt/codex/codex-path:/usr/local/bin:/usr/bin:/bin
RUN codex --version && claude --version

FROM base AS browser
RUN apt-get update && apt-get install -y --no-install-recommends curl gpg \
    && install -d -m 0755 /etc/apt/keyrings \
    && key=/etc/apt/keyrings/mozilla.asc \
    && curl -fsSL --retry 3 https://packages.mozilla.org/apt/repo-signing-key.gpg -o "$key" \
    && keyhome="$(mktemp -d)" \
    && fingerprints="$(gpg --batch --homedir "$keyhome" --with-colons --show-keys "$key" \
        | awk -F: '$1 == "pub" { primary=1 } $1 == "fpr" && primary { print $10; primary=0 }')" \
    && test "$fingerprints" = "35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3" \
    && rm -rf "$keyhome" && chmod 0644 "$key" \
    && echo "deb [signed-by=$key] https://packages.mozilla.org/apt mozilla main" > /etc/apt/sources.list.d/mozilla.list \
    && printf '%s\n' 'Package: firefox' 'Pin: origin packages.mozilla.org' 'Pin-Priority: 1000' \
        '' 'Package: firefox' 'Pin: release o=Ubuntu' 'Pin-Priority: -1' > /etc/apt/preferences.d/mozilla \
    && apt-get update && apt-get install -y --no-install-recommends firefox fonts-dejavu-core tar \
    && /usr/bin/firefox --version \
    && rm -rf /var/lib/apt/lists/*
