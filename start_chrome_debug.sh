#!/bin/bash
# Inicia Chrome con remote debugging habilitado
# Esto permite que el token_daemon pueda conectarse y refrescar el token

PORT=9222
CHROME="/usr/bin/google-chrome"
PROFILE="$HOME/.config/google-chrome"

# Verificar si ya hay un Chrome con debugging
if curl -s "http://localhost:$PORT/json/version" > /dev/null 2>&1; then
    echo "Chrome ya está corriendo con remote debugging en puerto $PORT"
    exit 0
fi

# Cerrar Chrome existente si está corriendo
if pgrep -x "chrome" > /dev/null; then
    echo "Cerrando Chrome existente..."
    pkill -x "chrome"
    sleep 2
fi

echo "Iniciando Chrome con remote debugging en puerto $PORT..."
echo "OneDrive se abrirá automáticamente."

# Iniciar Chrome con debugging
$CHROME \
    --remote-debugging-port=$PORT \
    --user-data-dir="$PROFILE" \
    --profile-directory="Default" \
    "https://shdgov-my.sharepoint.com" &

echo ""
echo "Chrome iniciado. Ahora puedes ejecutar:"
echo "  python3 token_daemon.py"
echo ""
echo "O para verificar el estado:"
echo "  python3 token_daemon.py --status"
