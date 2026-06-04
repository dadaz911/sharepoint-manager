#!/usr/bin/env python3
"""
Script auxiliar para mostrar instrucciones de actualización de token.
El token se actualiza automáticamente cuando Claude tiene acceso al navegador.
"""

import base64
import json
from datetime import datetime
from pathlib import Path


def decode_jwt_expiry(token):
    """Decodificar fecha de expiración del token"""
    try:
        parts = token.split('.')
        if len(parts) >= 2:
            # Decodificar payload
            payload = parts[1]
            # Agregar padding si es necesario
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            decoded = base64.urlsafe_b64decode(payload)
            data = json.loads(decoded)
            exp = data.get('exp')
            if exp:
                exp_date = datetime.fromtimestamp(int(exp))
                return exp_date
    except Exception:
        pass
    return None


def main():
    token_file = Path("/home/daniel/Desktop/Cargue a Onedrive/.token")
    progress_file = Path("/home/daniel/Desktop/Cargue a Onedrive/.upload_progress.json")

    print("=" * 50)
    print("  Estado de la subida a OneDrive")
    print("=" * 50)
    print()

    # Verificar token
    if token_file.exists():
        token = token_file.read_text().strip()
        exp_date = decode_jwt_expiry(token)
        if exp_date:
            now = datetime.now()
            if exp_date > now:
                remaining = (exp_date - now).total_seconds() / 60
                print(f"🔑 Token válido hasta: {exp_date.strftime('%H:%M:%S')}")
                print(f"   Tiempo restante: {remaining:.0f} minutos")
            else:
                print("❌ Token EXPIRADO")
                print("   Necesitas actualizar el token")
        else:
            print("⚠️  No se pudo verificar expiración del token")
    else:
        print("❌ Token no encontrado")

    print()

    # Verificar progreso
    if progress_file.exists():
        with open(progress_file) as f:
            progress = json.load(f)
        uploaded = len(progress.get("uploaded", []))
        errors = len(progress.get("errors", []))
        print(f"📊 Archivos subidos: {uploaded}")
        if errors > 0:
            print(f"❌ Errores: {errors}")
    else:
        print("📊 No hay progreso guardado")

    print()
    print("=" * 50)
    print("  Para continuar la subida:")
    print("  python3 subir_onedrive.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
