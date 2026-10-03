"""Comprueba que el menú «Proyecto» se construye con el pywebview real instalado (la parte visual solo se ve en un Mac)."""
import importlib.util, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("puente_rf", os.path.join(ROOT, "puente-rf.py"))
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)
import inspect
import webview
from webview.menu import Menu, MenuAction, MenuSeparator

menu = pr.project_menu()
assert len(menu) == 1 and isinstance(menu[0], Menu) and menu[0].title == "Proyecto", menu
acciones = [i for i in menu[0].items if isinstance(i, MenuAction)]
assert len(acciones) == 11 and all(callable(a.function) for a in acciones), acciones
assert any(isinstance(i, MenuSeparator) for i in menu[0].items)
assert "menu" in inspect.signature(webview.start).parameters, "esta versión de pywebview no admite menús"
assert "js_api" in inspect.signature(webview.create_window).parameters
assert hasattr(webview, "FileDialog") or hasattr(webview, "OPEN_DIALOG")
mon = pr.monitor_menu()
assert len(mon) == 1 and mon[0].title == "Monitor" and len([i for i in mon[0].items if isinstance(i, MenuAction)]) == 5, mon
print("menú Proyecto: correcto con pywebview", getattr(webview, "__version__", "?"))
