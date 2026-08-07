"""The closed error set of the pipeline (see CONTRACT.md)."""


class PipelineError(Exception):
    exit_code = 1


class ConfigError(PipelineError):
    exit_code = 2


class AdapterError(PipelineError):
    exit_code = 4


class PublishError(PipelineError):
    exit_code = 5
