from django.apps import AppConfig


class CommonConfig(AppConfig):
    """
    v2 shared-services app (frozen architecture §9). Holds no models; it is
    installed so its management commands (partition maintenance) are
    discoverable.
    """

    name = "common"
    verbose_name = "SparkLM v2 shared services"
