"""把 chromadb 的产品遥测换成空实现（详见 app/rag/store.py 的说明）。

overrides 库要求子类重写父类方法时必须加 @override，否则 raise TypeError。
这里显式加上装饰器，避免 importlib 加载时直接失败。
"""
from overrides import override
from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent


class NoopProductTelemetry(ProductTelemetryClient):
    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None
