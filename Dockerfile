FROM docker:27-cli

RUN apk add --no-cache bash curl jq flock python3

COPY swap.sh /usr/local/bin/swap.sh
COPY seed.sh /usr/local/bin/seed.sh
COPY loop.sh /usr/local/bin/loop.sh
COPY learned-bot/release.py /usr/local/lib/learned-bot/release.py
COPY learned-bot/deploy.sh /usr/local/bin/deploy-learned-bot.sh
RUN chmod +x /usr/local/bin/deploy-learned-bot.sh
RUN chmod +x /usr/local/bin/swap.sh /usr/local/bin/seed.sh /usr/local/bin/loop.sh

ENTRYPOINT ["/usr/local/bin/loop.sh"]
