"""
Metrics collection for Prometheus-compatible monitoring.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from collections import defaultdict
import threading


@dataclass
class MetricValue:
    """Single metric value with labels."""
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Counter:
    """
    Monotonically increasing counter metric.
    """
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()
    
    def inc(self, value: float = 1.0, **labels) -> None:
        """Increment the counter."""
        label_key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[label_key] += value
    
    def get(self, **labels) -> float:
        """Get current counter value."""
        label_key = tuple(sorted(labels.items()))
        return self._values.get(label_key, 0.0)
    
    def collect(self) -> List[MetricValue]:
        """Collect all metric values."""
        with self._lock:
            return [
                MetricValue(value=v, labels=dict(k))
                for k, v in self._values.items()
            ]


class Gauge:
    """
    Gauge metric that can go up and down.
    """
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()
    
    def set(self, value: float, **labels) -> None:
        """Set the gauge value."""
        label_key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[label_key] = value
    
    def inc(self, value: float = 1.0, **labels) -> None:
        """Increment the gauge."""
        label_key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[label_key] += value
    
    def dec(self, value: float = 1.0, **labels) -> None:
        """Decrement the gauge."""
        label_key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[label_key] -= value
    
    def get(self, **labels) -> float:
        """Get current gauge value."""
        label_key = tuple(sorted(labels.items()))
        return self._values.get(label_key, 0.0)
    
    def collect(self) -> List[MetricValue]:
        """Collect all metric values."""
        with self._lock:
            return [
                MetricValue(value=v, labels=dict(k))
                for k, v in self._values.items()
            ]


class Histogram:
    """
    Histogram metric for measuring distributions.
    """
    
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float('inf'))
    
    def __init__(
        self,
        name: str,
        description: str = "",
        labels: List[str] = None,
        buckets: tuple = None,
    ):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self.buckets = buckets or self.DEFAULT_BUCKETS
        
        self._counts: Dict[tuple, Dict[float, int]] = defaultdict(lambda: defaultdict(int))
        self._sums: Dict[tuple, float] = defaultdict(float)
        self._totals: Dict[tuple, int] = defaultdict(int)
        self._lock = threading.Lock()
    
    def observe(self, value: float, **labels) -> None:
        """Record an observation."""
        label_key = tuple(sorted(labels.items()))
        
        with self._lock:
            self._sums[label_key] += value
            self._totals[label_key] += 1
            
            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[label_key][bucket] += 1
    
    def get_percentile(self, percentile: float, **labels) -> Optional[float]:
        """Estimate percentile from histogram buckets."""
        label_key = tuple(sorted(labels.items()))
        
        with self._lock:
            total = self._totals.get(label_key, 0)
            if total == 0:
                return None
            
            target = total * percentile
            cumulative = 0
            prev_bucket = 0
            
            for bucket in sorted(self.buckets):
                count = self._counts[label_key].get(bucket, 0)
                cumulative += count
                
                if cumulative >= target:
                    # Linear interpolation
                    if count > 0:
                        fraction = (target - (cumulative - count)) / count
                        return prev_bucket + fraction * (bucket - prev_bucket)
                    return bucket
                
                prev_bucket = bucket
            
            return self.buckets[-1]
    
    def collect(self) -> Dict[str, Any]:
        """Collect histogram data."""
        with self._lock:
            result = {}
            for label_key, counts in self._counts.items():
                labels = dict(label_key)
                label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
                
                result[label_str] = {
                    "buckets": dict(counts),
                    "sum": self._sums.get(label_key, 0),
                    "count": self._totals.get(label_key, 0),
                }
            return result


class MetricsCollector:
    """
    Central metrics collector for all service metrics.
    """
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        
        # Default metrics
        self.request_count = self.counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status"],
        )
        self.request_duration = self.histogram(
            "http_request_duration_seconds",
            "HTTP request duration",
            ["method", "path"],
        )
        self.active_requests = self.gauge(
            "http_requests_active",
            "Active HTTP requests",
        )
    
    def counter(self, name: str, description: str = "", labels: List[str] = None) -> Counter:
        """Create or get a counter."""
        full_name = f"{self.service_name}_{name}"
        if full_name not in self._counters:
            self._counters[full_name] = Counter(full_name, description, labels)
        return self._counters[full_name]
    
    def gauge(self, name: str, description: str = "", labels: List[str] = None) -> Gauge:
        """Create or get a gauge."""
        full_name = f"{self.service_name}_{name}"
        if full_name not in self._gauges:
            self._gauges[full_name] = Gauge(full_name, description, labels)
        return self._gauges[full_name]
    
    def histogram(
        self,
        name: str,
        description: str = "",
        labels: List[str] = None,
        buckets: tuple = None,
    ) -> Histogram:
        """Create or get a histogram."""
        full_name = f"{self.service_name}_{name}"
        if full_name not in self._histograms:
            self._histograms[full_name] = Histogram(full_name, description, labels, buckets)
        return self._histograms[full_name]
    
    def collect_all(self) -> Dict[str, Any]:
        """Collect all metrics."""
        return {
            "counters": {name: c.collect() for name, c in self._counters.items()},
            "gauges": {name: g.collect() for name, g in self._gauges.items()},
            "histograms": {name: h.collect() for name, h in self._histograms.items()},
        }
    
    def to_prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        
        # Counters
        for name, counter in self._counters.items():
            lines.append(f"# HELP {name} {counter.description}")
            lines.append(f"# TYPE {name} counter")
            for mv in counter.collect():
                label_str = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
                if label_str:
                    lines.append(f"{name}{{{label_str}}} {mv.value}")
                else:
                    lines.append(f"{name} {mv.value}")
        
        # Gauges
        for name, gauge in self._gauges.items():
            lines.append(f"# HELP {name} {gauge.description}")
            lines.append(f"# TYPE {name} gauge")
            for mv in gauge.collect():
                label_str = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
                if label_str:
                    lines.append(f"{name}{{{label_str}}} {mv.value}")
                else:
                    lines.append(f"{name} {mv.value}")
        
        # Histograms
        for name, histogram in self._histograms.items():
            lines.append(f"# HELP {name} {histogram.description}")
            lines.append(f"# TYPE {name} histogram")
            data = histogram.collect()
            for label_str, values in data.items():
                base_labels = f"{{{label_str}}}" if label_str else ""
                
                # Bucket values
                cumulative = 0
                for bucket in sorted(histogram.buckets):
                    cumulative += values["buckets"].get(bucket, 0)
                    le = "+Inf" if bucket == float('inf') else str(bucket)
                    if label_str:
                        lines.append(f'{name}_bucket{{{label_str},le="{le}"}} {cumulative}')
                    else:
                        lines.append(f'{name}_bucket{{le="{le}"}} {cumulative}')
                
                # Sum and count
                if label_str:
                    lines.append(f"{name}_sum{{{label_str}}} {values['sum']}")
                    lines.append(f"{name}_count{{{label_str}}} {values['count']}")
                else:
                    lines.append(f"{name}_sum {values['sum']}")
                    lines.append(f"{name}_count {values['count']}")
        
        return "\n".join(lines)


class MetricsMiddleware:
    """FastAPI middleware for automatic metrics collection."""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    async def __call__(self, request, call_next):
        start_time = time.time()
        
        self.collector.active_requests.inc()
        
        try:
            response = await call_next(request)
            
            duration = time.time() - start_time
            
            self.collector.request_count.inc(
                method=request.method,
                path=request.url.path,
                status=str(response.status_code),
            )
            self.collector.request_duration.observe(
                duration,
                method=request.method,
                path=request.url.path,
            )
            
            return response
            
        finally:
            self.collector.active_requests.dec()
