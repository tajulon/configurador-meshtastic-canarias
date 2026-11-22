"""Interfaz de ventanas (tkinter) del Configurador Meshtastic Canarias (FAKETEC y MASN).

Toda la lógica (detección, bootloader, firmware, configuración, verificación, backup y
reinicio) es la del núcleo `configurar_nodo`; aquí solo se muestra y se pregunta.
"""
import io
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

TITULO = "Configurador Meshtastic Canarias (FAKETEC y MASN)"
AUTOR = "Realizado por Eduardo, EA8II"

# (clave, texto, fragmentos del núcleo que activan el paso)
PASOS = [
    ("buscar", "Buscar la placa", ("Buscando la placa",)),
    ("requisitos", "Comprobar bootloader, firmware y modelo", ("Comprobando requisitos",)),
    ("instalar", "Instalar / actualizar firmware (si hace falta)",
     ("modo bootloader (sin pulsar", "Placa en MODO BOOTLOADER")),
    ("aplicar", "Aplicar configuración y nombre", ("Aplicando configuración",)),
    ("verificar", "Verificar campo a campo", ("Esperando a que el nodo se reinicie", "Verificando campo")),
    ("reiniciar", "Reiniciar (Bluetooth activo)", ("Reiniciando el nodo",)),
]

# Avisos destacados: (fragmento en el registro, tipo, texto del aviso)
AVISOS = [
    ("PON LA PLACA EN MODO BOOTLOADER", "accion",
     "Pulsa el botón RESET de la placa DOS VECES seguidas (rápido)."),
    ("NO responde como Meshtastic", "accion",
     "La placa no tiene Meshtastic: pulsa RESET DOS VECES para instalarlo."),
    ("No detecto ninguna placa", "accion",
     "No se detecta ninguna placa. Revisa el cable USB (de datos) o pulsa RESET dos veces."),
    ("está ocupado por otro programa", "accion",
     "Puerto ocupado: cierra Meshtastic Web, la app o cualquier monitor serie."),
    ("Placa en MODO BOOTLOADER detectada", "info",
     "Placa en modo bootloader: instalando. NO la desconectes."),
    ("Nodo encontrado en", "info", "Placa detectada. Trabajando: no la desconectes."),
    ("Reiniciando el nodo", "info", "Reiniciando el nodo para dejar el Bluetooth activo..."),
]

COLORES = {
    "info": ("#0b4f8a", "#e3f0fb"),
    "accion": ("#7a4b00", "#fff1cc"),
    "ok": ("#14632d", "#dff3e4"),
    "error": ("#8a1111", "#fbe1e1"),
    "neutro": ("#333333", "#eeeeee"),
}


class Escritor(io.TextIOBase):
    """Redirige print() del núcleo al registro de la ventana."""

    def __init__(self, cola):
        self.cola = cola

    def write(self, s):
        if s:
            self.cola.put(("log", s))
        return len(s)

    def flush(self):
        pass


class App:
    def __init__(self, nucleo, args):
        self.n = nucleo
        self.args = args
        self.cola = queue.Queue()
        self.plantilla = None
        self.fw = None
        self.trabajando = False
        self.cerrando = False
        self.paso_actual = -1

        self.root = tk.Tk()
        self.root.title(TITULO)
        self.root.geometry("900x720")
        self.root.minsize(760, 600)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.al_cerrar)
        self._construir()

        # El núcleo escribe y pregunta a través de la ventana
        sys.stdout = sys.stderr = Escritor(self.cola)
        self.n.PREGUNTAR = self.preguntar_desde_hilo
        self.n.AL_PASO = lambda t: self.cola.put(("paso", t))

        self.root.after(100, self.procesar_cola)
        self.root.after(200, self.inicio)

    # ------------------------------------------------------------ construcción
    def _construir(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(4, weight=1)

        cab = tk.Frame(r, bg="#1f3b57")
        cab.grid(row=0, column=0, sticky="ew")
        tk.Label(cab, text="Configurador Meshtastic Canarias", font=("Segoe UI", 17, "bold"),
                 fg="white", bg="#1f3b57").pack(anchor="w", padx=16, pady=(10, 0))
        tk.Label(cab, text="Módulos FAKETEC y MASN · Nice!Nano / Pro Micro nRF52840  ·  " + AUTOR,
                 font=("Segoe UI", 10), fg="#cfe0f1", bg="#1f3b57").pack(anchor="w", padx=16, pady=(0, 10))

        info = ttk.Frame(r, padding=(16, 8, 16, 0))
        info.grid(row=1, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        ttk.Label(info, text="Firmware:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.lbl_fw = ttk.Label(info, text="comprobando la última versión...")
        self.lbl_fw.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(info, text="Plantilla:", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w")
        self.lbl_plantilla = ttk.Label(info, text=self.args.plantilla)
        self.lbl_plantilla.grid(row=1, column=1, sticky="w", padx=6)
        tk.Label(info, text="⚠  Canales y radio predeterminados para CANARIAS: canal principal LongFast + "
                            "«Canarias» + «Emergencias», EU_868, modo LONG_FAST. En la Península u otras "
                            "zonas pueden ser distintos: consulta a tu comunidad y ajusta plantilla.yaml.",
                 font=("Segoe UI", 9, "bold"), fg="#7a4b00", bg="#fff1cc", justify="left", anchor="w",
                 wraplength=820, padx=8, pady=5).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        form = ttk.LabelFrame(r, text=" Nuevo nodo ", padding=(12, 8))
        form.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Nombre del nodo:").grid(row=0, column=0, sticky="w")
        self.var_nombre = tk.StringVar()
        self.ent_nombre = ttk.Entry(form, textvariable=self.var_nombre, font=("Segoe UI", 11))
        self.ent_nombre.grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(form, text="ej. Prueba X7 (máx. 39)", foreground="#777").grid(row=0, column=2, sticky="w")
        ttk.Label(form, text="Nombre corto:").grid(row=1, column=0, sticky="w")
        self.var_corto = tk.StringVar()
        self.ent_corto = ttk.Entry(form, textvariable=self.var_corto, width=8, font=("Segoe UI", 11))
        self.ent_corto.grid(row=1, column=1, sticky="w", padx=8, pady=3)
        ttk.Label(form, text="ej. X7 (máx. 4)", foreground="#777").grid(row=1, column=2, sticky="w")
        self.lbl_error = ttk.Label(form, text="", foreground="#b00020")
        self.lbl_error.grid(row=2, column=0, columnspan=3, sticky="w")
        botones = ttk.Frame(form)
        botones.grid(row=0, column=3, rowspan=3, sticky="ns", padx=(12, 0))
        self.btn_config = ttk.Button(botones, text="Configurar nodo", command=self.al_configurar, state="disabled")
        self.btn_config.pack(fill="x", ipady=6)
        self.btn_cancelar = ttk.Button(botones, text="Cancelar", command=self.al_cancelar, state="disabled")
        self.btn_cancelar.pack(fill="x", pady=(6, 0))
        self.ent_nombre.bind("<Return>", lambda e: self.ent_corto.focus_set())
        self.ent_corto.bind("<Return>", lambda e: self.al_configurar())

        estado = ttk.Frame(r, padding=(16, 0))
        estado.grid(row=3, column=0, sticky="ew")
        estado.columnconfigure(1, weight=1)
        pasos = ttk.LabelFrame(estado, text=" Progreso ", padding=(10, 6))
        pasos.grid(row=0, column=0, sticky="nsw")
        self.lbl_pasos = []
        for i, (_k, texto, _f) in enumerate(PASOS):
            ico = tk.Label(pasos, text="○", font=("Segoe UI", 11), fg="#999", width=2)
            ico.grid(row=i, column=0)
            lbl = tk.Label(pasos, text=texto, font=("Segoe UI", 10), fg="#555", anchor="w")
            lbl.grid(row=i, column=1, sticky="w")
            self.lbl_pasos.append((ico, lbl))
        self.aviso = tk.Label(estado, text="Preparando...", font=("Segoe UI", 12, "bold"),
                              wraplength=430, justify="left", anchor="w", padx=14, pady=10)
        self.aviso.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self.poner_aviso("neutro", "Preparando...")

        regf = ttk.LabelFrame(r, text=" Registro detallado ", padding=(6, 4))
        regf.grid(row=4, column=0, sticky="nsew", padx=16, pady=8)
        regf.columnconfigure(0, weight=1)
        regf.rowconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(regf, font=("Consolas", 9), wrap="word", height=12,
                                             state="disabled", background="#fbfbfb")
        self.log.grid(row=0, column=0, sticky="nsew")

        pie = ttk.Frame(r, padding=(16, 0, 16, 10))
        pie.grid(row=5, column=0, sticky="ew")
        ttk.Button(pie, text="Abrir carpeta de backups", command=self.abrir_backups).pack(side="left")
        ttk.Button(pie, text="Ver LEEME", command=self.abrir_leeme).pack(side="left", padx=6)
        self.var_top = tk.BooleanVar(value=False)
        ttk.Checkbutton(pie, text="Siempre visible", variable=self.var_top,
                        command=lambda: self.root.attributes("-topmost", self.var_top.get())).pack(side="left", padx=12)
        ttk.Button(pie, text="Salir", command=self.al_cerrar).pack(side="right")

    # ------------------------------------------------------------ utilidades UI
    def poner_aviso(self, tipo, texto):
        fg, bg = COLORES[tipo]
        self.aviso.configure(text=texto, fg=fg, bg=bg)

    def escribir_log(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")
        for frag, tipo, texto in AVISOS:
            if frag in s:
                self.poner_aviso(tipo, texto)
        if "Placa en MODO BOOTLOADER" in s:
            self.marcar_paso_clave("instalar")

    def reiniciar_pasos(self):
        self.paso_actual = -1
        for ico, lbl in self.lbl_pasos:
            ico.configure(text="○", fg="#999")
            lbl.configure(fg="#555", font=("Segoe UI", 10))

    def marcar_paso_clave(self, clave):
        idx = [k for k, _t, _f in PASOS].index(clave)
        if idx <= self.paso_actual:
            return   # nunca retroceder (p. ej. al volver a buscar tras instalar firmware)
        for i in range(max(self.paso_actual, 0), idx):
            if i >= 0 and i <= self.paso_actual:
                self._estado_paso(i, "hecho")
        for i in range(self.paso_actual + 1, idx):
            self._estado_paso(i, "omitido" if PASOS[i][0] == "instalar" else "hecho")
        self.paso_actual = idx
        self._estado_paso(idx, "activo")

    def _estado_paso(self, i, est):
        ico, lbl = self.lbl_pasos[i]
        if est == "activo":
            ico.configure(text="◉", fg="#0b6bcb")
            lbl.configure(fg="#0b4f8a", font=("Segoe UI", 10, "bold"))
        elif est == "hecho":
            ico.configure(text="✔", fg="#1e8e3e")
            lbl.configure(fg="#333", font=("Segoe UI", 10))
        elif est == "omitido":
            ico.configure(text="–", fg="#999")
            lbl.configure(fg="#999", font=("Segoe UI", 10))
        elif est == "error":
            ico.configure(text="✘", fg="#c5221f")
            lbl.configure(fg="#c5221f", font=("Segoe UI", 10, "bold"))

    def al_paso(self, texto):
        for k, _t, frags in PASOS:
            if any(f in texto for f in frags):
                self.marcar_paso_clave(k)
                return

    # ------------------------------------------------------------ cola (hilo -> UI)
    def procesar_cola(self):
        try:
            while True:
                m = self.cola.get_nowait()
                tipo = m[0]
                if tipo == "log":
                    self.escribir_log(m[1])
                elif tipo == "paso":
                    self.al_paso(m[1])
                elif tipo == "pregunta":
                    self._responder(*m[1:])
                elif tipo == "fw":
                    self._fw_listo(m[1])
                elif tipo == "fin":
                    self._fin(*m[1:])
        except queue.Empty:
            pass
        self.root.after(100, self.procesar_cola)

    def preguntar_desde_hilo(self, tipo, texto):
        """Llamado por el núcleo (hilo de trabajo): pregunta en la ventana y espera."""
        ev, caja = threading.Event(), {}
        self.cola.put(("pregunta", tipo, texto, ev, caja))
        while not ev.wait(0.2):
            if self.n.CANCELADO.is_set():
                return None if tipo == "texto" else False
        return caja.get("r")

    def _responder(self, tipo, texto, ev, caja):
        self.root.deiconify()
        self.root.lift()
        if tipo == "si_no":
            caja["r"] = messagebox.askyesno(TITULO, texto, parent=self.root)
        else:
            caja["r"] = simpledialog.askstring(TITULO, texto, parent=self.root)
        ev.set()

    # ------------------------------------------------------------ flujo
    def inicio(self):
        if not self.mostrar_descargo():
            self.root.destroy()
            return
        self.plantilla, error = self.n.cargar_plantilla(self.args.plantilla)
        if error:
            self.poner_aviso("error", "No se puede continuar: " + error)
            messagebox.showerror(TITULO, error, parent=self.root)
            return
        print(f"Plantilla: {self.args.plantilla}")
        threading.Thread(target=self._hilo_firmware, daemon=True).start()

    def _hilo_firmware(self):
        try:
            fw = self.n.elegir_firmware()
        except Exception as e:  # nunca debería pasar: elegir_firmware ya cae al incluido
            print(f"ERROR al elegir firmware: {e}")
            fw = None
        self.cola.put(("fw", fw))

    def _fw_listo(self, fw):
        self.fw = fw
        if not fw:
            self.poner_aviso("error", "No se pudo preparar el firmware.")
            return
        _ruta, ver, origen = fw
        txt = {"incluido": "incluido en el programa", "descargado": "descargado de Meshtastic"}.get(origen, origen)
        self.lbl_fw.configure(text=f"{ver}  ({txt})")
        self.btn_config.configure(state="normal")
        self.poner_aviso("neutro", "Escribe el nombre del nodo y el nombre corto, conecta la placa por USB "
                                   "y pulsa «Configurar nodo».")
        self.ent_nombre.focus_set()

    def validar(self):
        nombre, corto = self.var_nombre.get().strip(), self.var_corto.get().strip()
        if not nombre:
            return None, None, "Escribe el nombre del nodo."
        if len(nombre.encode("utf-8")) > 39:
            return None, None, "El nombre del nodo es demasiado largo (máx. 39 caracteres sin tildes)."
        if not corto:
            return None, None, "Escribe el nombre corto."
        if len(corto.encode("utf-8")) > 4:
            return None, None, "El nombre corto admite como máximo 4 caracteres (sin tildes)."
        return nombre, corto, ""

    def al_configurar(self):
        if self.trabajando or not self.fw or not self.plantilla:
            return
        nombre, corto, err = self.validar()
        self.lbl_error.configure(text=err)
        if err:
            return
        self.trabajando = True
        self.n.CANCELADO.clear()
        self.reiniciar_pasos()
        for w in (self.ent_nombre, self.ent_corto, self.btn_config):
            w.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.poner_aviso("info", "Conecta la placa por USB. El programa la detecta solo.")
        threading.Thread(target=self._hilo_configurar, args=(nombre, corto), daemon=True).start()

    def _hilo_configurar(self, nombre, corto):
        ok, err = False, ""
        try:
            ok = self.n.configurar_uno(self.plantilla, self.args.backups, self.fw, nombre=nombre, corto=corto)
        except KeyboardInterrupt:
            err = "cancelado"
            print("\nCancelado por el usuario.")
        except Exception as e:
            err = str(e)
            print(f"\nERROR inesperado: {e}")
        self.cola.put(("fin", ok, err, dict(self.n.ULTIMO)))

    def _fin(self, ok, err, ultimo):
        self.trabajando = False
        self.n.CANCELADO.clear()
        for w in (self.ent_nombre, self.ent_corto, self.btn_config):
            w.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        if ok:
            for i in range(len(PASOS)):
                ico, _ = self.lbl_pasos[i]
                if ico.cget("text") not in ("–",):
                    self._estado_paso(i, "hecho")
            txt = (f"✔ Nodo configurado correctamente: {ultimo.get('nombre')} ({ultimo.get('corto')}) "
                   f"en {ultimo.get('port')}. Bluetooth activo.\nPuedes conectar el siguiente.")
            self.poner_aviso("ok", txt)
            self.ent_nombre.focus_set()
            self.ent_nombre.select_range(0, "end")
        else:
            if self.paso_actual >= 0:
                self._estado_paso(self.paso_actual, "error")
            if err == "cancelado":
                self.poner_aviso("neutro", "Cancelado. Puedes volver a intentarlo.")
            else:
                self.poner_aviso("error", "✘ El nodo NO se ha configurado. Revisa el registro detallado."
                                 + (f"\n{err}" if err else ""))
        if self.cerrando:
            self.root.destroy()

    def al_cancelar(self):
        if self.trabajando:
            self.n.CANCELADO.set()
            self.btn_cancelar.configure(state="disabled")
            self.poner_aviso("accion", "Cancelando... (si se estaba configurando, se termina el paso en curso "
                                       "y se reinicia el nodo)")

    def al_cerrar(self):
        if self.trabajando:
            if not messagebox.askyesno(TITULO, "Hay un nodo en proceso. ¿Cancelar y salir?\n"
                                               "(Se terminará el paso en curso para no dejar la placa a medias.)",
                                       parent=self.root):
                return
            self.cerrando = True
            self.al_cancelar()
            return
        self.root.destroy()

    def abrir_backups(self):
        os.makedirs(self.args.backups, exist_ok=True)
        os.startfile(self.args.backups)

    def abrir_leeme(self):
        ruta = os.path.join(self.n.BASE_DIR, "LEEME.txt")
        if os.path.isfile(ruta):
            os.startfile(ruta)
        else:
            messagebox.showinfo(TITULO, "No encuentro LEEME.txt junto al programa.", parent=self.root)

    # ------------------------------------------------------------ descargo
    def mostrar_descargo(self):
        d = tk.Toplevel(self.root)
        d.title(TITULO + " · Condiciones de uso")
        d.transient(self.root)
        d.resizable(False, False)
        res = {"ok": False}
        txt = scrolledtext.ScrolledText(d, width=70, height=30, font=("Consolas", 9), wrap="word")
        txt.insert("1.0", self.n.PRESENTACION.strip("\n"))
        txt.configure(state="disabled")
        txt.pack(padx=12, pady=(12, 6))
        b = ttk.Frame(d)
        b.pack(pady=(0, 12))

        def aceptar():
            res["ok"] = True
            d.destroy()
        ttk.Button(b, text="Acepto las condiciones", command=aceptar).pack(side="left", padx=6, ipady=3)
        ttk.Button(b, text="Salir", command=d.destroy).pack(side="left", padx=6, ipady=3)
        d.protocol("WM_DELETE_WINDOW", d.destroy)
        d.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - d.winfo_width()) // 2
        y = self.root.winfo_rooty() + 40
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.grab_set()
        d.focus_set()
        self.root.wait_window(d)
        return res["ok"]

    def ejecutar(self):
        self.root.mainloop()
        return 0


def ejecutar(nucleo):
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # texto nítido en pantallas HiDPI
    except Exception:
        pass
    args = nucleo.argumentos()
    return App(nucleo, args).ejecutar()
