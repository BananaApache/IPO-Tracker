from django.apps import AppConfig

class IposConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ipos'

    def ready(self):
        import archive.backend.ipos.signals
        