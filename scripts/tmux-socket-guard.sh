#!/bin/bash
# Vigia root do socket tmux da MAIA (cron de minuto). Incidente 24/11/2026: uma
# limpeza de /tmp apagou /tmp/tmux-<uid> e o bot (tmux send-keys) ficou mudo.
# SIGUSR1 faz o tmux server recriar o socket sem derrubar a sessao. Idempotente.
for pid in $(ps -eo pid=,comm= | awk "\$2==\"tmux:\" && \$3==\"server\"{print \$1}"); do
  uid=$(stat -c %u /proc/$pid) || continue
  args=$(tr "\0" " " < /proc/$pid/cmdline)
  sock=$(printf "%s" "$args" | sed -nE "s/.* -S ([^ ]+).*/\1/p")
  if [ -z "$sock" ]; then
    lbl=$(printf "%s" "$args" | sed -nE "s/.* -L ([^ ]+).*/\1/p"); sock="/tmp/tmux-$uid/${lbl:-default}"
  fi
  [ -S "$sock" ] && continue
  d=$(dirname "$sock"); mkdir -p "$d" && chown "$uid" "$d" && chmod 700 "$d"
  kill -USR1 "$pid" && logger -t tmux-socket-guard "socket $sock recriado (server pid $pid)"
done
