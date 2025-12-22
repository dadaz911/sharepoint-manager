#!/bin/bash
# SharePoint Token Service - Inicio automático
# Solo requiere intervención manual si la sesión de Microsoft expira (90 días)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/service.log"
PID_FILE="$SCRIPT_DIR/service.pid"
CHROME_DEBUG_PORT=9222
ONEDRIVE_URL="https://shdgov-my.sharepoint.com"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

check_chrome() {
    ss -tlnp 2>/dev/null | grep -q ":$CHROME_DEBUG_PORT" && return 0 || return 1
}

start_chrome() {
    log "Iniciando Chrome con debugging port $CHROME_DEBUG_PORT..."
    
    # Matar instancias previas de Chrome
    pkill -f "chrome.*remote-debugging-port" 2>/dev/null
    sleep 2
    
    # Iniciar Chrome con debugging
    DISPLAY=:0 google-chrome \
        --remote-debugging-port=$CHROME_DEBUG_PORT \
        --user-data-dir=/home/daniel/.config/google-chrome \
        --no-first-run \
        --disable-default-apps \
        "$ONEDRIVE_URL" &
    
    # Esperar a que Chrome inicie
    for i in {1..30}; do
        if check_chrome; then
            log "Chrome iniciado correctamente en puerto $CHROME_DEBUG_PORT"
            return 0
        fi
        sleep 1
    done
    
    log "ERROR: Chrome no pudo iniciar"
    return 1
}

start_daemon() {
    log "Iniciando token daemon..."
    cd "$SCRIPT_DIR"
    
    # Verificar si el daemon ya está corriendo
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        log "Daemon ya está corriendo (PID: $(cat $PID_FILE))"
        return 0
    fi
    
    # Iniciar daemon en background
    nohup python3 token_daemon.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    log "Daemon iniciado (PID: $!)"
}

stop_service() {
    log "Deteniendo servicio..."
    
    if [ -f "$PID_FILE" ]; then
        kill $(cat "$PID_FILE") 2>/dev/null
        rm -f "$PID_FILE"
    fi
    
    pkill -f "chrome.*remote-debugging-port" 2>/dev/null
    log "Servicio detenido"
}

status() {
    echo "=== SharePoint Token Service Status ==="
    
    if check_chrome; then
        echo "Chrome: ✅ Corriendo (puerto $CHROME_DEBUG_PORT)"
    else
        echo "Chrome: ❌ No está corriendo"
    fi
    
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "Daemon: ✅ Corriendo (PID: $(cat $PID_FILE))"
    else
        echo "Daemon: ❌ No está corriendo"
    fi
    
    echo ""
    python3 "$SCRIPT_DIR/token_daemon.py" --status 2>/dev/null
}

case "$1" in
    start)
        start_chrome && sleep 5 && start_daemon
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service
        sleep 2
        start_chrome && sleep 5 && start_daemon
        ;;
    status)
        status
        ;;
    refresh)
        python3 "$SCRIPT_DIR/token_daemon.py" --once
        ;;
    *)
        echo "Uso: $0 {start|stop|restart|status|refresh}"
        echo ""
        echo "  start   - Inicia Chrome + daemon"
        echo "  stop    - Detiene todo"
        echo "  restart - Reinicia servicio"
        echo "  status  - Muestra estado"
        echo "  refresh - Refresca token una vez"
        exit 1
        ;;
esac
