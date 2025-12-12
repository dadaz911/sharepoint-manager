#!/bin/bash

# Script para subir archivos a OneDrive usando el token de SharePoint
# Uso: ./upload_to_onedrive.sh

# Configuración
BASE_URL="https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web"
DEST_FOLDER="/personal/dzuniga_shd_gov_co1/Documents/Pruebas"
SOURCE_DIR="/home/daniel/Desktop/Cargue a Onedrive"
LOG_FILE="$SOURCE_DIR/upload_log.txt"
ERROR_LOG="$SOURCE_DIR/upload_errors.txt"
PROGRESS_FILE="$SOURCE_DIR/upload_progress.txt"
TOKEN_FILE="$SOURCE_DIR/.token"
PARALLEL_JOBS=5

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Función para obtener token del navegador
get_token_from_browser() {
    echo -e "${YELLOW}Por favor, asegúrate de que OneDrive esté abierto en Chrome/Edge${NC}"
    echo "Presiona Enter cuando esté listo..."
    read
}

# Función para crear carpeta en OneDrive
create_folder() {
    local folder_path="$1"
    local token="$2"

    curl -s -X POST \
        "${BASE_URL}/folders/add(url='${DEST_FOLDER}${folder_path}')" \
        -H "Authorization: Bearer $token" \
        -H "Accept: application/json;odata=verbose" \
        -H "Content-Type: application/json" > /dev/null 2>&1
}

# Función para subir un archivo
upload_file() {
    local file_path="$1"
    local token="$2"
    local relative_path="${file_path#$SOURCE_DIR/}"
    local dest_path="$DEST_FOLDER/$relative_path"
    local folder_path=$(dirname "$dest_path")
    local file_name=$(basename "$file_path")

    # URL encode del nombre
    local encoded_name=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$file_name'))")
    local encoded_folder=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$folder_path'))")

    # Subir archivo
    response=$(curl -s -w "%{http_code}" -o /tmp/upload_response.json -X POST \
        "${BASE_URL}/GetFolderByServerRelativeUrl('${encoded_folder}')/Files/add(url='${encoded_name}',overwrite=true)" \
        -H "Authorization: Bearer $token" \
        -H "Accept: application/json;odata=verbose" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@$file_path" 2>/dev/null)

    if [[ "$response" == "200" ]] || [[ "$response" == "201" ]]; then
        echo -e "${GREEN}✓${NC} $relative_path"
        echo "$file_path" >> "$PROGRESS_FILE"
        return 0
    else
        echo -e "${RED}✗${NC} $relative_path (HTTP $response)"
        echo "$file_path|$response" >> "$ERROR_LOG"
        return 1
    fi
}

# Función para crear estructura de carpetas
create_folder_structure() {
    local token="$1"
    echo -e "${YELLOW}Creando estructura de carpetas...${NC}"

    find "$SOURCE_DIR" -type d ! -path "$SOURCE_DIR" ! -name ".*" | while read dir; do
        relative_dir="${dir#$SOURCE_DIR}"
        encoded_dir=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$relative_dir'))")

        curl -s -X POST \
            "${BASE_URL}/folders" \
            -H "Authorization: Bearer $token" \
            -H "Accept: application/json;odata=verbose" \
            -H "Content-Type: application/json" \
            -d "{\"ServerRelativeUrl\":\"${DEST_FOLDER}${relative_dir}\"}" > /dev/null 2>&1

        echo -n "."
    done
    echo -e "\n${GREEN}Estructura de carpetas creada${NC}"
}

# Función principal
main() {
    echo "========================================"
    echo "  Subida masiva a OneDrive"
    echo "========================================"

    # Verificar token
    if [[ ! -f "$TOKEN_FILE" ]]; then
        echo -e "${RED}Token no encontrado.${NC}"
        echo "Ejecuta primero: ./get_token.sh"
        exit 1
    fi

    TOKEN=$(cat "$TOKEN_FILE")

    # Verificar que el token funciona
    test_response=$(curl -s -o /dev/null -w "%{http_code}" \
        "${BASE_URL}/GetFolderByServerRelativeUrl('${DEST_FOLDER}')" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Accept: application/json")

    if [[ "$test_response" != "200" ]]; then
        echo -e "${RED}Token inválido o expirado.${NC}"
        echo "Ejecuta: ./get_token.sh para obtener uno nuevo"
        exit 1
    fi

    echo -e "${GREEN}Token válido${NC}"

    # Crear estructura de carpetas primero
    create_folder_structure "$TOKEN"

    # Contar archivos
    total_files=$(find "$SOURCE_DIR" -type f \( -name "*.pdf" -o -name "*.PDF" \) ! -path "*/.*" | wc -l)
    echo -e "Total de PDFs a subir: ${YELLOW}$total_files${NC}"

    # Archivos ya subidos
    uploaded=0
    if [[ -f "$PROGRESS_FILE" ]]; then
        uploaded=$(wc -l < "$PROGRESS_FILE")
        echo -e "Ya subidos anteriormente: ${GREEN}$uploaded${NC}"
    fi

    pending=$((total_files - uploaded))
    echo -e "Pendientes: ${YELLOW}$pending${NC}"
    echo ""

    if [[ $pending -eq 0 ]]; then
        echo -e "${GREEN}¡Todos los archivos ya fueron subidos!${NC}"
        exit 0
    fi

    echo "Iniciando subida..."
    echo "Presiona Ctrl+C para pausar (podrás continuar después)"
    echo ""

    # Subir archivos
    count=0
    find "$SOURCE_DIR" -type f \( -name "*.pdf" -o -name "*.PDF" \) ! -path "*/.*" | while read file; do
        # Saltar si ya fue subido
        if [[ -f "$PROGRESS_FILE" ]] && grep -qF "$file" "$PROGRESS_FILE"; then
            continue
        fi

        ((count++))
        echo -ne "\r[$count/$pending] "
        upload_file "$file" "$TOKEN"

        # Pequeña pausa para no sobrecargar
        sleep 0.1
    done

    echo ""
    echo -e "${GREEN}¡Subida completada!${NC}"

    # Mostrar resumen
    if [[ -f "$ERROR_LOG" ]]; then
        errors=$(wc -l < "$ERROR_LOG")
        if [[ $errors -gt 0 ]]; then
            echo -e "${RED}Archivos con errores: $errors${NC}"
            echo "Ver: $ERROR_LOG"
        fi
    fi
}

main "$@"
