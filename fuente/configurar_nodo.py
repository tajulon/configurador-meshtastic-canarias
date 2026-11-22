"""Configurador Meshtastic Canarias (FAKETEC y MASN).

Pide nombre largo y corto, espera a que se conecte un nodo con firmware Meshtastic,
le aplica la plantilla completa con ese nombre, verifica campo a campo y guarda un backup.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
import time

import yaml
from google.protobuf.json_format import MessageToDict
from serial.tools import list_ports

FROZEN = getattr(sys, "frozen", False)          # True cuando se ejecuta como .exe
PY = sys.executable
# Carpeta donde están plantilla.yaml y backups\ (junto al .exe, o la raíz del programa)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESENTACION = """
============================================================
   CONFIGURADOR MESHTASTIC CANARIAS (FAKETEC Y MASN)
   Módulos con Nice!Nano / Pro Micro nRF52840
   Realizado por Eduardo, EA8II
============================================================

 Este programa configura módulos FAKETEC y MASN basados en
 Nice!Nano / Pro Micro (nRF52840) con firmware Meshtastic: les
 aplica la configuración de plantilla.yaml y el nombre de nodo
 que indiques.

 ¡ATENCIÓN! CONFIGURACIÓN PARA CANARIAS
 --------------------------------------
 Los canales de esta plantilla (principal LongFast + "Canarias"
 + "Emergencias") y la radio (EU_868, modo LONG_FAST) son los
 predeterminados de la red Meshtastic de CANARIAS. En la
 Península u otras zonas los canales y el modo pueden ser
 distintos: consulta a tu comunidad local y ajusta
 plantilla.yaml antes de usar el programa.

 REQUISITOS DE LA PLACA
 ----------------------
 1. Bootloader UF2 (las Nice!Nano lo traen de fábrica). Si es
    anterior a 0.9.2, el programa ofrece actualizarlo (incluido).
 2. Firmware Meshtastic para "nRF52 Pro-micro DIY". El programa
    instala o actualiza SIEMPRE al último estable (con Internet);
    sin Internet, el incluido (2.7.26).
 Si la placa ya tiene Meshtastic, el programa la pone él solo en
 modo bootloader para actualizarla. Si está nueva o sin firmware,
 tendrás que pulsar el botón RESET dos veces cuando se te pida.
 El programa NO configurará una placa que no tenga ambos.
 Una placa SIN bootloader no se puede preparar con este programa:
 necesita un programador SWD (se explica si ocurre).

 DESCARGO DE RESPONSABILIDAD
 ---------------------------
 * Software de USO EDUCATIVO, sin fines comerciales.
 * Se proporciona "tal cual", SIN NINGUNA GARANTÍA de
   funcionamiento ni de idoneidad para ningún propósito.
 * El autor no se hace responsable de daños en los equipos,
   pérdida de datos o configuraciones, ni de cualquier otro
   perjuicio derivado de su uso.
 * El usuario es el único responsable de usarlo correctamente y
   de cumplir la normativa de radio aplicable (frecuencias,
   potencia y ciclo de trabajo).
 * Al continuar aceptas estas condiciones.
"""

DEFAULT_NAME = re.compile(r"^Meshtastic [0-9a-fA-F]{4}$")
ADAFRUIT_VID = 0x239A


# ---------------------------------------------------------------- utilidades
def titulo(t):
    print()
    print("=" * 60)
    print("  " + t)
    print("=" * 60)


# Enganches para la interfaz de ventanas (None = modo consola)
PREGUNTAR = None      # callable(tipo, texto) -> str | bool   (tipo: "texto" o "si_no")
AL_PASO = None        # callable(texto) llamado en cada paso
import threading
CANCELADO = threading.Event()


def dormir(s):
    """time.sleep que se interrumpe si el usuario cancela (modo ventana)."""
    if CANCELADO.wait(s):
        raise KeyboardInterrupt


def paso(t):
    print(f"\n==> {t}", flush=True)
    if AL_PASO:
        AL_PASO(t)


def cli(args, timeout):
    """Ejecuta el CLI de meshtastic. Devuelve (codigo, salida)."""
    try:
        cmd = [PY, "--meshtastic-cli", *args] if FROZEN else [PY, "-m", "meshtastic", *args]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        r = subprocess.run(
            cmd, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # sin ventanas negras
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "timeout"


def leer(texto):
    """input() robusto: quita BOM/espacios; si se cierra la entrada, cancela."""
    if PREGUNTAR:
        v = PREGUNTAR("texto", texto)
        if v is None:
            raise KeyboardInterrupt
        return v.strip()
    try:
        return input(texto).replace("﻿", "").replace("ï»¿", "").strip()
    except EOFError:
        raise KeyboardInterrupt


def pedir(texto, max_bytes):
    while True:
        v = leer(texto)
        if not v:
            print("   No puede estar vacío.")
        elif len(v.encode("utf-8")) > max_bytes:
            print(f"   Demasiado largo (máximo {max_bytes} caracteres sin tildes).")
        else:
            return v


def si_no(texto):
    if PREGUNTAR:
        return bool(PREGUNTAR("si_no", texto.strip()))
    while True:
        v = leer(texto + " (s/n): ").lower()
        if v in ("s", "si", "sí", "y"):
            return True
        if v in ("n", "no"):
            return False


# ---------------------------------------------------------------- detección
def puertos_candidatos():
    ps = list(list_ports.comports())
    nrf = [p.device for p in ps if p.vid == ADAFRUIT_VID]
    if nrf:
        return nrf
    return [p.device for p in ps if p.vid is not None]  # cualquier USB-serie


def sondear(port, info=None):
    """('ok', owner) | ('ocupado', None) | ('no', None).
    Si se pasa un dict en `info`, lo rellena con hwModel / firmwareVersion / pioEnv."""
    code, out = cli(["--port", port, "--timeout", "20", "--info"], timeout=60)
    m = re.search(r"^Owner: (.*)$", out, re.M)
    if code == 0 and m:
        if info is not None:
            for k in ("hwModel", "firmwareVersion", "pioEnv"):
                mm = re.search(r'"%s":\s*"([^"]*)"' % k, out)
                info[k] = mm.group(1) if mm else ""
        return "ok", m.group(1).strip()
    if "in use by another process" in out or "PermissionError" in out:
        return "ocupado", None
    return "no", None


# ---------------------------------------------------------------- requisitos
HW_FAKETEC = "NRF52_PROMICRO_DIY"   # hwModel que reporta el firmware Meshtastic para FAKETEC / MASN (Pro Micro)
FW_MINIMO = (2, 7)                  # la plantilla usa campos de la serie 2.7

INSTR_BOOTLOADER = """
   ---------------------------------------------------------------
   CÓMO COMPROBAR / INSTALAR EL BOOTLOADER
   ---------------------------------------------------------------
   * Con la placa conectada por USB, pulsa DOS VECES SEGUIDAS el
     botón RESET (o puentea RST con GND dos veces).
   * Si aparece una unidad USB nueva (p. ej. NICENANO) que contiene
     el fichero INFO_UF2.TXT, la placa TIENE bootloader: el programa
     la detecta e instala el firmware (y actualiza el bootloader si
     es antiguo) automáticamente.
   * Si no aparece ninguna unidad, la placa NO tiene bootloader UF2
     (o está dañada). Hay que grabarlo con un programador SWD
     (J-Link, ST-Link, Raspberry Pi Pico con "debugprobe"...):
       - Bootloader: Adafruit_nRF52_Bootloader, variante "nice_nano"
         (github.com/adafruit/Adafruit_nRF52_Bootloader/releases).
       - Conecta SWDIO, SWCLK, GND y 3V3 a los pads de la placa y
         graba el .hex con nrfjprog, pyOCD u OpenOCD.
     Este programa NO puede hacerlo: requiere hardware adicional.
   ---------------------------------------------------------------"""

INSTR_RESET = """
   ---------------------------------------------------------------
   PON LA PLACA EN MODO BOOTLOADER
   ---------------------------------------------------------------
   Con la placa conectada por USB, pulsa DOS VECES SEGUIDAS (rápido)
   el botón RESET (o puentea RST con GND dos veces).
   Aparecerá una unidad USB (p. ej. NICENANO). El programa la detecta
   y se encarga del resto: no copies nada a mano.
   ---------------------------------------------------------------"""

# ---------------------------------------------------------------- ficheros UF2
FW_TARGET = "nrf52_promicro_diy_tcxo"
FW_INCLUIDO = "firmware-nrf52_promicro_diy_tcxo-2.7.26.54e0d8d.uf2"
BL_INCLUIDO = "update-nice_nano_bootloader-0.9.2_nosd.uf2"
BL_VERSION = (0, 9, 2)
FAM_NRF52840 = 0xADA52840
FW_LIST_URL = "https://api.meshtastic.org/github/firmware/list"
FW_FILE_URL = ("https://raw.githubusercontent.com/meshtastic/meshtastic.github.io/master/"
               "firmware-{v}/firmware-" + FW_TARGET + "-{v}.uf2")
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "ConfiguradorMeshtastic", "firmware")


def recurso(nombre):
    """Ruta a un fichero incluido en el programa (dentro del .exe o en ../uf2)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "uf2", nombre)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "uf2", nombre)


def version3(v):
    nums = re.findall(r"\d+", v or "")[:3]
    nums = [int(n) for n in nums] + [0] * (3 - len(nums))
    return tuple(nums)


def uf2_valido(ruta, familia=None):
    try:
        import struct
        d = open(ruta, "rb").read()
        if len(d) < 512 * 10 or len(d) % 512:
            return False
        for i in range(0, len(d), 512):
            b = d[i:i + 512]
            m0, m1, flags, _a, _s, _b, _n, fam = struct.unpack("<IIIIIIII", b[:32])
            if m0 != 0x0A324655 or m1 != 0x9E5D5157 or struct.unpack("<I", b[508:])[0] != 0x0AB16F30:
                return False
            if familia is not None and (flags & 0x2000) and fam != familia:
                return False
        return True
    except OSError:
        return False


def elegir_firmware():
    """Devuelve (ruta_uf2, version, origen). Con Internet: el último estable para este
    dispositivo (como flasher.meshtastic.org). Sin Internet o si falla: el incluido."""
    import json, urllib.request
    incl = recurso(FW_INCLUIDO)
    v_incl = re.search(r"-(\d+\.\d+\.\d+\.[0-9a-f]+)\.uf2$", FW_INCLUIDO).group(1)
    paso("Comprobando la última versión del firmware Meshtastic")
    try:
        req = urllib.request.Request(FW_LIST_URL, headers={"User-Agent": "ConfiguradorMeshtasticCanarias"})
        with urllib.request.urlopen(req, timeout=10) as r:
            lista = json.load(r)
        v_ult = lista["releases"]["stable"][0]["id"].lstrip("v")
    except Exception:
        print(f"   Sin conexión a Internet: se usará el firmware incluido {v_incl}.")
        return incl, v_incl, "incluido"
    if version3(v_ult) <= version3(v_incl):
        print(f"   Última versión estable: {v_ult}. Coincide con la incluida en el programa.")
        return incl, v_incl, "incluido"
    destino = os.path.join(CACHE_DIR, f"firmware-{FW_TARGET}-{v_ult}.uf2")
    if not uf2_valido(destino, FAM_NRF52840):
        print(f"   Hay una versión más nueva: {v_ult}. Descargando...")
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            req = urllib.request.Request(FW_FILE_URL.format(v=v_ult), headers={"User-Agent": "ConfiguradorMeshtasticCanarias"})
            with urllib.request.urlopen(req, timeout=60) as r, open(destino + ".part", "wb") as f:
                f.write(r.read())
            os.replace(destino + ".part", destino)
        except Exception as e:
            print(f"   No se pudo descargar ({e}). Se usará el incluido {v_incl}.")
            return incl, v_incl, "incluido"
        if not uf2_valido(destino, FAM_NRF52840):
            print(f"   El fichero descargado no es válido. Se usará el incluido {v_incl}.")
            return incl, v_incl, "incluido"
    print(f"   Se usará el firmware {v_ult} (descargado de Meshtastic).")
    return destino, v_ult, "descargado"


def entrar_bootloader(port, espera_s=25):
    """Con Meshtastic funcionando, le pide por USB que entre en modo bootloader UF2
    (equivale al doble RESET). Devuelve True si aparece la unidad del bootloader."""
    cli(["--port", port, "--timeout", "30", "--enter-dfu"], timeout=60)
    fin = time.time() + espera_s
    while time.time() < fin:
        if unidades_uf2():
            return True
        time.sleep(0.5)
    return False


def copiar_a_uf2(origen, letra, nombre):
    """Copia un .uf2 a la unidad del bootloader y espera a que la placa se reinicie."""
    import shutil
    try:
        shutil.copyfile(origen, os.path.join(letra, nombre))
    except OSError:
        pass   # es normal que la unidad desaparezca justo al terminar la copia
    fin = time.time() + 60
    while time.time() < fin:
        if not os.path.exists(os.path.join(letra, "INFO_UF2.TXT")):
            return True
        time.sleep(0.5)
    return False


def unidades_uf2():
    """Unidades en modo bootloader UF2: [(letra, info_dict)]."""
    import ctypes
    res = []
    mask = ctypes.windll.kernel32.GetLogicalDrives() if os.name == "nt" else 0
    for i in range(26):
        if not mask & (1 << i):
            continue
        letra = f"{chr(65 + i)}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(letra) != 2:   # 2 = extraíble
            continue
        f = os.path.join(letra, "INFO_UF2.TXT")
        try:
            if not os.path.isfile(f):
                continue
            txt = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        info = {"version": "", "modelo": "", "board": "", "softdevice": ""}
        m = re.search(r"^SoftDevice:\s*(.+)$", txt, re.M)
        info["softdevice"] = m.group(1).strip() if m else "?"
        m = re.search(r"UF2 Bootloader\s+(\S+)", txt)
        info["version"] = m.group(1) if m else "?"
        m = re.search(r"^Model:\s*(.+)$", txt, re.M)
        info["modelo"] = m.group(1).strip() if m else "?"
        m = re.search(r"^Board-ID:\s*(.+)$", txt, re.M)
        info["board"] = m.group(1).strip() if m else "?"
        res.append((letra, info))
    return res


def version_tupla(v):
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def comprobar_requisitos(info, fw_disp, ya_actualizado=False):
    """Comprueba el nodo encontrado. Devuelve 'seguir', 'actualizar' o 'cancelar'."""
    hw, fw, env = info.get("hwModel", ""), info.get("firmwareVersion", ""), info.get("pioEnv", "")
    _ruta, v_disp, origen = fw_disp
    paso("Comprobando requisitos de la placa")
    print("   [OK] Bootloader UF2 presente (el firmware Meshtastic de esta placa se")
    print("        instala a través de él).")
    print(f"   [OK] Firmware Meshtastic {fw or '?'} funcionando ({env or 'entorno ?'}).")
    if hw and hw != HW_FAKETEC:
        print(f"   [!!] El modelo de placa es «{hw}», no «{HW_FAKETEC}» (FAKETEC / MASN).")
        print("        La plantilla está pensada para FAKETEC y MASN; en otra placa puede no ser válida.")
        if not si_no("        ¿Continuar de todas formas?"):
            return "cancelar"
    elif hw:
        print(f"   [OK] Modelo de placa: {hw} (FAKETEC / MASN).")
    if version3(fw) < version3(v_disp):
        if not ya_actualizado:
            print(f"   [!!] Firmware instalado {fw or '?'}; disponible {v_disp} ({origen}).")
            print("        Se actualiza automáticamente antes de configurar.")
            return "actualizar"
        print(f"   [!!] Tras actualizar, la placa sigue con {fw or '?'} (se esperaba {v_disp}).")
        if not si_no("        ¿Continuar con la versión que tiene?"):
            return "cancelar"
    elif version3(fw) > version3(v_disp):
        print(f"   [OK] Firmware {fw} más nuevo que el último estable ({v_disp}): no se toca.")
    else:
        print(f"   [OK] El firmware está al día ({v_disp}).")
    return "seguir"


def gestionar_bootloader(letra, inf, fw_disp):
    """Placa en modo bootloader: ofrece actualizar el bootloader y/o instalar el firmware.
    Devuelve 'bootloader' (se ha actualizado, volverá a aparecer), 'firmware' (instalado)
    o 'nada'."""
    print(f"\n   Placa en MODO BOOTLOADER detectada en la unidad {letra}")
    print(f"     Bootloader UF2 {inf['version']}  |  {inf['modelo']}  |  {inf['board']}")
    print(f"     SoftDevice: {inf['softdevice']}")
    print("   [OK] Bootloader presente.")
    if "6.1.1" not in inf["softdevice"]:
        print("   [!!] El firmware Meshtastic para nRF52 necesita el SoftDevice S140 6.1.1.")
        print("        Con otro SoftDevice puede no arrancar (habría que regrabar el")
        print("        bootloader completo por SWD).")
    es_nicenano = "nicenano" in inf["board"].lower().replace("!", "") or "nice!nano" in inf["modelo"].lower()
    blv = version3(inf["version"])
    if es_nicenano and blv < BL_VERSION:
        v = ".".join(map(str, BL_VERSION))
        print(f"   [!!] El bootloader {inf['version']} es anterior a {v} (incluido en el programa).")
        if si_no(f"        ¿Actualizar el bootloader a {v} ahora? (recomendado)"):
            print("   Actualizando bootloader... NO desconectes la placa ni pulses nada.")
            copiar_a_uf2(recurso(BL_INCLUIDO), letra, "update.uf2")
            print("   Bootloader enviado. La placa se reinicia; espera a que vuelva la unidad USB...")
            return "bootloader"
    elif es_nicenano:
        print(f"   [OK] Bootloader al día ({inf['version']}).")
    else:
        print("   [--] Bootloader no reconocido como nice!nano: no se ofrece actualizarlo.")

    ruta, v_fw, origen = fw_disp
    print(f"   Instalando firmware Meshtastic {v_fw} ({origen})... NO desconectes la placa.")
    copiar_a_uf2(ruta, letra, "firmware.uf2")
    print("   Firmware instalado. La placa se reinicia con Meshtastic (puede tardar ~20 s)...")
    return "firmware"


def esperar_nodo(fw_disp, solo_bootloader=False):
    """Espera a una placa con Meshtastic. Si aparece en modo bootloader, ofrece instalar
    bootloader/firmware. Con solo_bootloader=True ignora nodos ya funcionando (se está
    esperando a que el usuario ponga la placa en bootloader para actualizarla)."""
    paso("Buscando la placa (bootloader y firmware Meshtastic)...")
    if solo_bootloader == "manual":
        print(INSTR_RESET)
    elif solo_bootloader:
        pass   # ya está en modo bootloader: se detecta enseguida
    else:
        print("   La configuración SOLO se aplicará a una placa con bootloader UF2 y")
        print("   firmware Meshtastic. Se detecta sola; " +
              ("pulsa «Cancelar» para cancelar." if PREGUNTAR else "Ctrl+C para cancelar."))
    avisado_ocupado, avisado_sin_fw, gestionadas = set(), set(), set()
    avisado_nada = False
    t_inicio = time.time()
    t_bl = None          # momento en que se envió una actualización de bootloader
    sin_respuesta = {}
    while True:
        # 1) ¿Placa en modo bootloader?
        uf2 = unidades_uf2()
        presentes = {l for l, _ in uf2}
        gestionadas &= presentes          # si la unidad desaparece, se volverá a gestionar
        for letra, inf in uf2:
            if letra in gestionadas:
                continue
            gestionadas.add(letra)
            r = gestionar_bootloader(letra, inf, fw_disp)
            if r == "bootloader":
                t_bl = time.time()
            elif r == "firmware":
                solo_bootloader = False
                t_inicio, t_bl, avisado_nada = time.time(), None, False
                sin_respuesta.clear()
                avisado_sin_fw.clear()
        if uf2:
            dormir(2)
            continue   # no sondear puertos mientras está en bootloader
        if t_bl and time.time() - t_bl > 30:
            t_bl = None
            print("   La unidad del bootloader no ha vuelto sola: pulsa DOS VECES RESET para")
            print("   volver al modo bootloader e instalar el firmware.")
        if solo_bootloader:
            dormir(1)
            continue

        # 2) ¿Nodo Meshtastic respondiendo por USB?
        encontrados = []
        candidatos = puertos_candidatos()
        for port in candidatos:
            dormir(0)
            info = {}
            estado, owner = sondear(port, info)
            if estado == "ok":
                sin_respuesta.pop(port, None)
                encontrados.append((port, owner, info))
            elif estado == "ocupado":
                if port not in avisado_ocupado:
                    avisado_ocupado.add(port)
                    print(f"   ! {port} está ocupado por otro programa. Cierra Meshtastic Web,")
                    print("     la app de Meshtastic o cualquier monitor serie que lo esté usando.")
            else:
                sin_respuesta[port] = sin_respuesta.get(port, 0) + 1
                if sin_respuesta[port] >= 2 and port not in avisado_sin_fw:
                    avisado_sin_fw.add(port)
                    print(f"\n   [--] Hay una placa en {port} pero NO responde como Meshtastic:")
                    print("        no tiene el firmware Meshtastic (o tiene otro). No se configurará")
                    print("        hasta instalarlo. El programa lo instala por ti:")
                    print(INSTR_RESET)

        # 3) Nada conectado durante un rato -> cómo comprobar el bootloader
        if not candidatos and not avisado_nada and time.time() - t_inicio > 20:
            avisado_nada = True
            print("\n   No detecto ninguna placa con firmware. Comprueba el cable USB (que sea")
            print("   de datos, no solo de carga). Si la placa está nueva o sin firmware:")
            print(INSTR_RESET)
            print(INSTR_BOOTLOADER)

        if len(encontrados) == 1:
            return encontrados[0]
        if len(encontrados) > 1:
            lista = "\n".join(f"     {i}) {p}  ->  {o}" for i, (p, o, _) in enumerate(encontrados, 1))
            print("\n   Hay varios nodos conectados:\n" + lista)
            while True:
                v = leer("Hay varios nodos conectados:\n" + lista + "\n\n¿Cuál configuro? (número): ")
                if v.isdigit() and 1 <= int(v) <= len(encontrados):
                    return encontrados[int(v) - 1]
        dormir(3)


def esperar_reinicio(port, max_s=150):
    dormir(8)
    fin = time.time() + max_s
    while time.time() < fin:
        if port in [p.device for p in list_ports.comports()]:
            estado, owner = sondear(port)
            if estado == "ok":
                return owner
        dormir(3)
    return None


# ---------------------------------------------------------------- lectura completa
def leer_nodo(port):
    import meshtastic.serial_interface
    try:
        iface = meshtastic.serial_interface.SerialInterface(port, timeout=90)
    except TypeError:
        iface = meshtastic.serial_interface.SerialInterface(port)
    try:
        node = iface.localNode
        full = lambda m: MessageToDict(m, always_print_fields_with_no_presence=True)
        return {
            "owner": iface.getLongName(),
            "owner_short": iface.getShortName(),
            "channel_url": node.getURL(),
            "canned_messages": iface.getCannedMessage() or "",
            "ringtone": iface.getRingtone() or "",
            "config": {k: v for k, v in full(node.localConfig).items() if isinstance(v, dict)},
            "module_config": {k: v for k, v in full(node.moduleConfig).items() if isinstance(v, dict)},
        }
    finally:
        iface.close()


def aplanar(d, pref=""):
    out = {}
    for k, v in d.items():
        key = f"{pref}.{k}" if pref else k
        if isinstance(v, dict):
            out.update(aplanar(v, key))
        else:
            out[key] = v
    return out


def comparar(esperado, real):
    """Devuelve (errores, avisos)."""
    errores, avisos = [], []
    e = aplanar({"config": esperado.get("config", {}), "module_config": esperado.get("module_config", {})})
    r = aplanar({"config": real["config"], "module_config": real["module_config"]})
    for k, v in e.items():
        rv = r.get(k, "<no existe>")
        if rv == v or (isinstance(v, float) and isinstance(rv, (int, float)) and abs(rv - v) < 1e-6):
            continue
        # 0 = "valor por defecto": el firmware a veces lo sustituye por su valor real
        if v in (0, "0") and re.search(r"(Interval|Secs)$", k):
            avisos.append(f"{k}: plantilla 0 (por defecto), el firmware guarda {rv}")
            continue
        # Texto vacío en la plantilla: el CLI no puede vaciar un texto ya puesto en el nodo
        if v == "" and isinstance(rv, str):
            avisos.append(f"{k}: la plantilla lo deja vacío, el nodo conserva {rv!r}")
            continue
        errores.append(f"{k}: esperado {v!r}, en el nodo {rv!r}")
    for k in ("owner", "owner_short", "channel_url", "canned_messages"):
        if k in esperado and str(esperado[k]) != str(real.get(k)):
            errores.append(f"{k}: esperado {esperado[k]!r}, en el nodo {real.get(k)!r}")
    return errores, avisos


def guardar_backup(datos, carpeta):
    os.makedirs(carpeta, exist_ok=True)
    seguro = re.sub(r"[^\w\-]+", "_", datos["owner_short"]) or "nodo"
    ruta = os.path.join(carpeta, f"{seguro}_{datetime.datetime.now():%Y%m%d-%H%M%S}.yaml")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("# Backup completo del nodo tras configurarlo (contiene sus claves: guardalo en privado)\n")
        yaml.safe_dump(datos, f, allow_unicode=True, sort_keys=True)
    return ruta


# ---------------------------------------------------------------- flujo principal
def aplicar(port, plantilla):
    fd, tmp = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# start of Meshtastic configure yaml\n")
        yaml.safe_dump(plantilla, f, allow_unicode=True, sort_keys=True)
    try:
        for intento in range(1, 4):
            code, out = cli(["--port", port, "--configure", tmp], timeout=300)
            if code == 0 and "Writing modified configuration" in out:
                return True
            print(f"   Intento {intento} fallido, reintentando...")
            ult = [l for l in out.strip().splitlines() if l.strip()][-3:]
            for l in ult:
                print("     " + l)
            time.sleep(10)
        return False
    finally:
        os.remove(tmp)


ULTIMO = {}   # datos del último nodo configurado (para la interfaz de ventanas)


def configurar_uno(plantilla_base, carpeta_backups, fw_disp, nombre=None, corto=None):
    titulo("NUEVO NODO")
    ULTIMO.clear()
    if nombre is None:
        nombre = pedir("Nombre del nodo (largo, ej. Prueba X7): ", 39)
    if corto is None:
        corto = pedir("Nombre corto (máx. 4, ej. X7): ", 4)
    print(f"Nombre: {nombre}  |  Nombre corto: {corto}")

    print("\nConecta la placa por USB. El programa instala o actualiza el firmware Meshtastic")
    print("él solo. Solo si la placa está nueva o sin Meshtastic te pedirá pulsar RESET dos")
    print("veces.")

    solo_bl = False
    while True:
        port, owner, info = esperar_nodo(fw_disp, solo_bootloader=solo_bl)
        print(f"\n   Nodo encontrado en {port}: {owner}")
        r = comprobar_requisitos(info, fw_disp, ya_actualizado=bool(solo_bl))
        if r == "cancelar":
            print("   Cancelado. No se ha tocado el nodo.")
            return False
        if r == "actualizar":
            paso("Poniendo la placa en modo bootloader (sin pulsar RESET)")
            if entrar_bootloader(port):
                print("   Hecho: la placa está en modo bootloader.")
                solo_bl = "auto"
            else:
                print("   No ha entrado sola en modo bootloader. Hazlo a mano:")
                solo_bl = "manual"
            continue
        break
    nombre_actual = owner.rsplit(" (", 1)[0]
    if not DEFAULT_NAME.match(nombre_actual) and nombre_actual != nombre:
        print(f"   ¡Ojo! Este nodo ya tiene nombre propio: «{owner}».")
        if not si_no("   ¿Seguro que quieres reconfigurarlo como «%s»?" % nombre):
            print("   Cancelado. No se ha tocado el nodo.")
            return False

    plantilla = dict(plantilla_base)
    plantilla["owner"] = nombre
    plantilla["owner_short"] = corto

    try:
        resultado = aplicar_y_verificar(port, plantilla)
    finally:
        # El firmware apaga el Bluetooth mientras hay un cliente conectado por USB/serie y
        # no lo vuelve a encender hasta que ese cliente se desconecta o pasan 15 minutos.
        # Reiniciamos SIEMPRE el nodo y NO volvemos a abrir la conexión: arranca limpio
        # con el Bluetooth activo, haya ido bien o mal.
        paso("Reiniciando el nodo para dejar el Bluetooth activo")
        if not reiniciar_sin_conectar(port):
            print("   Aviso: no he podido confirmar el reinicio. Si el Bluetooth no aparece,")
            print("   desconecta y vuelve a conectar la placa (o pulsa RESET una vez).")
    if resultado is None:
        return False
    real, avisos = resultado
    ruta = guardar_backup(real, carpeta_backups)

    titulo("NODO CONFIGURADO CORRECTAMENTE")
    print(f"  Puerto:  {port}")
    print(f"  Nombre:  {real['owner']} ({real['owner_short']})")
    print("  Configuración idéntica a la plantilla.")
    for a in avisos:
        print(f"  Nota: {a}")
    print(f"  Backup:  {ruta}")
    print("  Bluetooth activo: ya puedes conectarte desde la app (PIN según plantilla).")
    print("  No abras ningún programa por USB si vas a usar Bluetooth (lo apaga).")
    ULTIMO.update(port=port, nombre=real["owner"], corto=real["owner_short"], backup=ruta, avisos=avisos)
    return True


def aplicar_y_verificar(port, plantilla):
    """Devuelve (datos_del_nodo, avisos) si queda igual que la plantilla, o None."""
    for ronda in (1, 2):
        paso("Aplicando configuración completa y nombre" + (" (segundo intento)" if ronda == 2 else ""))
        if not aplicar(port, plantilla):
            print("   ERROR: no se pudo aplicar la configuración.")
            return None
        paso("Esperando a que el nodo se reinicie")
        if esperar_reinicio(port) is None:
            print("   ERROR: el nodo no ha vuelto a responder tras reiniciarse.")
            return None
        paso("Verificando campo a campo")
        real = leer_nodo(port)
        errores, avisos = comparar(plantilla, real)
        if not errores:
            return real, avisos
        print("   Diferencias encontradas:")
        for e in errores:
            print("     - " + e)
    print("\n   RESULTADO: el nodo NO ha quedado igual que la plantilla (ver diferencias arriba).")
    return None


def reiniciar_sin_conectar(port):
    """Manda reiniciar el nodo y espera a que el puerto USB desaparezca y vuelva,
    sin abrir una sesión de configuración (que apagaría otra vez el Bluetooth)."""
    code, out = cli(["--port", port, "--timeout", "30", "--reboot"], timeout=90)
    if code != 0:
        return False
    visto_fuera = False
    fin = time.time() + 60
    while time.time() < fin:
        presente = port in [p.device for p in list_ports.comports()]
        if not presente:
            visto_fuera = True
        elif visto_fuera:
            time.sleep(3)  # margen para que termine de arrancar
            return True
        time.sleep(0.5)
    # Si nunca vimos desaparecer el puerto, igualmente se mandó el reinicio
    return code == 0


def cargar_plantilla(ruta):
    """Devuelve (plantilla, None) o (None, mensaje_de_error)."""
    if not os.path.isfile(ruta):
        return None, (f"No encuentro la plantilla: {ruta}\n"
                      "Debe estar en la misma carpeta que el programa, con el nombre plantilla.yaml")
    try:
        with open(ruta, encoding="utf-8") as f:
            base = yaml.safe_load(f)
    except Exception as e:
        return None, f"La plantilla no se puede leer: {e}"
    for k in ("owner", "owner_short"):
        base.pop(k, None)
    sec = base.get("config", {}).get("security", {})
    if sec.get("privateKey") or sec.get("publicKey"):
        return None, "La plantilla contiene claves privadas/públicas. Quítalas antes de usarla."
    for f in (FW_INCLUIDO, BL_INCLUIDO):
        if not uf2_valido(recurso(f)):
            return None, f"Falta o está dañado el fichero incluido {f}."
    return base, None


def argumentos():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plantilla", default=os.path.join(BASE_DIR, "plantilla.yaml"))
    ap.add_argument("--backups", default=os.path.join(BASE_DIR, "backups"))
    ap.add_argument("--consola", action="store_true", help="usar el modo texto en vez de la ventana")
    return ap.parse_args()


def main():
    a = argumentos()

    print(PRESENTACION)
    try:
        leer("Pulsa Enter para aceptar y continuar (Ctrl+C para salir)... ")
    except KeyboardInterrupt:
        print("\nNo aceptado. Saliendo sin tocar nada.")
        return 0

    base, error = cargar_plantilla(a.plantilla)
    if error:
        print("ERROR: " + error)
        return 2
    print(f"Plantilla: {a.plantilla}")

    try:
        fw_disp = elegir_firmware()
        while True:
            configurar_uno(base, a.backups, fw_disp)
            if not si_no("\n¿Configurar otro nodo?"):
                break
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--meshtastic-cli":
        # El propio .exe hace de CLI de meshtastic (equivale a "python -m meshtastic ...")
        from meshtastic.__main__ import main as mt_main
        sys.argv = ["meshtastic"] + sys.argv[2:]
        sys.exit(mt_main())
    if "--consola" not in sys.argv:
        # Modo normal: ventana de Windows
        import interfaz
        sys.exit(interfaz.ejecutar(sys.modules[__name__]))
    try:
        rc = main()
    except Exception as e:  # que la ventana no se cierre sin explicar nada
        print(f"\nERROR inesperado: {e}")
        rc = 1
    if FROZEN:
        try:
            input("\nPulsa Enter para cerrar...")
        except EOFError:
            pass
    sys.exit(rc)
