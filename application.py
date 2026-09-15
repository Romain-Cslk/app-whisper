"""Assemble l'application FastAPI avec les fonctionnalités optionnelles."""

import server as core_server
from history_feature import install_history_feature

app = core_server.app
shutdown_background_services = core_server.shutdown_background_services

install_history_feature(app, core_server)
