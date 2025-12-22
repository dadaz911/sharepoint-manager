#!/bin/bash
# SharePoint Token Service - Wayland Edition (Seguro)
# Usa cage (compositor minimalista) + wayvnc (acceso remoto)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/wayland-service.log"
PID_FILE_CAGE="$SCRIPT_DIR/cage.pid"
PID_FILE_VNC="$SCRIPT_DIR/wayvnc.pid"
PID_FILE_DAEMON="$SCRIPT_DIR/daemon.pid"

CHROME_DEBUG_PORT=9222
VNC_PORT=5900
ONEDRIVE_URL="https://shdgov-my.sharepoint.com"

# Directorio de datos separado para Chrome con debugging
CHROME_DATA_DIR="/home/daniel/.config/chrome-sharepoint"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

check_cage() {
    [ -f "$PID_FILE_CAGE" ] && kill -0 $(cat "$PID_FILE_CAGE") 2>/dev/null
}

check_vnc() {
    [ -f "$PID_FILE_VNC" ] && kill -0 $(cat "$PID_FILE_VNC") 2>/dev/null
}

check_daemon() {
    [ -f "$PID_FILE_DAEMON" ] && kill -0 $(cat "$PID_FILE_DAEMON") 2>/dev/null
}

check_chrome_debug() {
    ss -tlnp 2>/dev/null | grep -q ":$CHROME_DEBUG_PORT"
}

setup_chrome_profile() {
    # Crear directorio de perfil si no existe
    if [ ! -d "$CHROME_DATA_DIR" ]; then
        log "Creando perfil de Chrome para SharePoint..."
        mkdir -p "$CHROME_DATA_DIR"
        
        # Copiar cookies y sesión del perfil existente si existe
        if [ -d "/home/daniel/.config/google-chrome/Default" ]; then
            cp -r /home/daniel/.config/google-chrome/Default/Cookies* "$CHROME_DATA_DIR/" 2>/dev/null
            cp -r /home/daniel/.config/google-chrome/Default/Login\ Data* "$CHROME_DATA_DIR/" 2>/dev/null
            cp -r /home/daniel/.config/google-chrome/Default/Web\ Data* "$CHROME_DATA_DIR/" 2>/dev/null
            log "Datos de sesión copiados del perfil principal"
        fi
    fi
}

start_wayland() {
    log "Iniciando cage (Wayland compositor)..."
    
    setup_chrome_profile
    
    # Crear directorio para socket Wayland
    export XDG_RUNTIME_DIR="/tmp/sharepoint-wayland-$USER"
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
    
    # Iniciar cage con Chrome en modo Wayland headless
    WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 cage -- google-chrome \
        --ozone-platform=wayland \
        --remote-debugging-port=$CHROME_DEBUG_PORT --remote-allow-origins=* \
        --user-data-dir="$CHROME_DATA_DIR" \
        --no-first-run \
        --no-sandbox \
        --disable-gpu \
        --disable-dev-shm-usage \
        --disable-software-rasterizer \
        --disable-extensions \
        "$ONEDRIVE_URL" &
    
    CAGE_PID=$!
    echo $CAGE_PID > "$PID_FILE_CAGE"
    log "Cage iniciado (PID: $CAGE_PID)"
    
    # Esperar a que Chrome inicie
    for i in {1..45}; do
        if check_chrome_debug; then
            log "Chrome iniciado en Wayland (puerto debug: $CHROME_DEBUG_PORT)"
            return 0
        fi
        sleep 1
    done
    
    log "ERROR: Chrome no respondió en tiempo esperado"
    return 1
}

start_vnc() {
    log "Iniciando wayvnc (puerto $VNC_PORT)..."
    
    export XDG_RUNTIME_DIR="/tmp/sharepoint-wayland-$USER"
    
    # Esperar a que Wayland esté listo
    sleep 3
    
    # Buscar el socket de Wayland
    WAYLAND_SOCK="$(ls $XDG_RUNTIME_DIR/wayland-* 2>/dev/null | head -1)"
    if [ -n "$WAYLAND_SOCK" ]; then
        export WAYLAND_DISPLAY="$(basename $WAYLAND_SOCK)"
        log "Usando Wayland display: $WAYLAND_DISPLAY"
    fi
    
    wayvnc 0.0.0.0 $VNC_PORT >> "$LOG_FILE" 2>&1 &
    VNC_PID=$!
    echo $VNC_PID > "$PID_FILE_VNC"
    
    sleep 2
    if check_vnc; then
        log "wayvnc iniciado en puerto $VNC_PORT"
        return 0
    else
        log "WARN: wayvnc pudo no haber iniciado correctamente"
        return 1
    fi
}

start_daemon() {
    log "Iniciando token daemon..."
    
    if check_daemon; then
        log "Daemon ya está corriendo"
        return 0
    fi
    
    cd "$SCRIPT_DIR"
    nohup .venv/bin/python token_daemon.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE_DAEMON"
    log "Token daemon iniciado (PID: $!)"
}

start_all() {
    log "=== Iniciando SharePoint Token Service (Wayland) ==="
    
    start_wayland
    if [ $? -eq 0 ]; then
        start_vnc
        sleep 3
        start_daemon
        log "=== Servicio iniciado completamente ==="
        status
    else
        log "ERROR: No se pudo iniciar el servicio"
        return 1
    fi
}

stop_all() {
    log "=== Deteniendo servicio ==="
    
    for pidfile in "$PID_FILE_DAEMON" "$PID_FILE_VNC" "$PID_FILE_CAGE"; do
        if [ -f "$pidfile" ]; then
            kill $(cat "$pidfile") 2>/dev/null
            rm -f "$pidfile"
        fi
    done
    
    pkill -f "cage.*google-chrome" 2>/dev/null
    pkill -f "wayvnc" 2>/dev/null
    log "Servicio detenido"
}

status() {
    echo "=========================================="
    echo "  SharePoint Token Service (Wayland)"
    echo "=========================================="
    echo ""
    
    if check_cage; then
        echo "Cage (Wayland):  ✅ Corriendo (PID: $(cat $PID_FILE_CAGE 2>/dev/null))"
    else
        echo "Cage (Wayland):  ❌ No está corriendo"
    fi
    
    if check_chrome_debug; then
        echo "Chrome Debug:    ✅ Puerto $CHROME_DEBUG_PORT activo"
    else
        echo "Chrome Debug:    ❌ Puerto $CHROME_DEBUG_PORT inactivo"
    fi
    
    if check_vnc; then
        echo "wayvnc:          ✅ Puerto $VNC_PORT (PID: $(cat $PID_FILE_VNC 2>/dev/null))"
    else
        echo "wayvnc:          ❌ No está corriendo"
    fi
    
    if check_daemon; then
        echo "Token Daemon:    ✅ Corriendo (PID: $(cat $PID_FILE_DAEMON 2>/dev/null))"
    else
        echo "Token Daemon:    ❌ No está corriendo"
    fi
    
    echo ""
    echo "--- Estado del Token ---"
    python3 "$SCRIPT_DIR/token_daemon.py" --status 2>/dev/null
}

case "$1" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        sleep 2
        start_all
        ;;
    status)
        status
        ;;
    vnc)
        echo "Conectar via VNC: vncviewer 100.101.165.103:$VNC_PORT"
        ;;
    refresh)
        python3 "$SCRIPT_DIR/token_daemon.py" --once
        ;;
    *)
        echo "SharePoint Token Service (Wayland Edition)"
        echo ""
        echo "Uso: $0 {start|stop|restart|status|vnc|refresh}"
        echo ""
        echo "  start   - Inicia Wayland + Chrome + VNC + Daemon"
        echo "  stop    - Detiene todo"
        echo "  restart - Reinicia servicio completo"
        echo "  status  - Muestra estado de componentes"
        echo "  vnc     - Muestra info de conexión VNC"
        echo "  refresh - Refresca token manualmente"
        ;;
esac
