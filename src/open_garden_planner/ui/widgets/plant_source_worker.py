"""Background source retrieval; store mutations stay on the Qt thread."""
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from open_garden_planner.services.plant_source_import import fetch_plant_source


class _Signals(QObject):
    ready = pyqtSignal(object, str)


class PlantSourceWorker(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = _Signals()

    def run(self):
        try:
            plant = fetch_plant_source(self.url)
        except Exception as error:
            self.signals.ready.emit(None, str(error))
        else:
            self.signals.ready.emit(plant, "")
