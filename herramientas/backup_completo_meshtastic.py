"""Backup COMPLETO de un nodo Meshtastic: todos los campos, incluidos los que valen false/0.
Uso: python full_backup.py COM24 carpeta_salida
"""
import sys, os, json, yaml
import meshtastic.serial_interface
from meshtastic.__main__ import export_profile
from google.protobuf.json_format import MessageToDict

port, outdir = sys.argv[1], sys.argv[2]
iface = meshtastic.serial_interface.SerialInterface(port)
node = iface.localNode

def full(msg):
    return MessageToDict(msg, always_print_fields_with_no_presence=True)

def b64(d):
    sec = d.get("security")
    if sec:
        for k in ("privateKey", "publicKey"):
            if sec.get(k):
                sec[k] = "base64:" + sec[k]
        sec["adminKey"] = ["base64:" + k for k in sec.get("adminKey", [])]
    return clean(d)

def clean(d):
    # listas vacias y wifiPsk vacio: el CLI no los admite al cargar (y vacio == valor por defecto)
    for k, v in list(d.items()):
        if isinstance(v, dict):
            clean(v)
        elif v == [] or (k == "wifiPsk" and v == ""):
            del d[k]
    return d

cfg = b64({k: v for k, v in full(node.localConfig).items() if isinstance(v, dict)})
mod = clean({k: v for k, v in full(node.moduleConfig).items() if isinstance(v, dict)})

out = {
    "owner": iface.getLongName(),
    "owner_short": iface.getShortName(),
    "channel_url": node.getURL(),
    "canned_messages": iface.getCannedMessage() or "",
    "ringtone": iface.getRingtone() or "",
    "config": cfg,
    "module_config": mod,
}
pos = (iface.getMyNodeInfo() or {}).get("position")
if pos and node.localConfig.position.fixed_position:
    out["location"] = {"lat": pos.get("latitude", 0.0), "lon": pos.get("longitude", 0.0), "alt": pos.get("altitude", 0)}

with open(os.path.join(outdir, "X5-backup-completo.yaml"), "w", encoding="utf-8") as f:
    f.write("# Backup COMPLETO Meshtastic (todos los campos, incluidos false/0)\n")
    yaml.safe_dump(out, f, allow_unicode=True, sort_keys=True)

# Copia binaria exacta (formato .cfg oficial, aplicable con --configure)
with open(os.path.join(outdir, "X5-backup-completo.cfg"), "wb") as f:
    f.write(export_profile(iface))

# Info extra de referencia (canales detallados, metadata, firmware)
extra = {
    "channels": [full(c) for c in node.channels or []],
    "myInfo": iface.getMyNodeInfo(),
    "metadata": full(iface.metadata) if getattr(iface, "metadata", None) else None,
}
with open(os.path.join(outdir, "X5-info-extra.json"), "w", encoding="utf-8") as f:
    json.dump(extra, f, indent=2, ensure_ascii=False, default=str)

iface.close()
print("OK")
