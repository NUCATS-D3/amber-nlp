"""Background-job boundary for long-running extraction and evaluation work.

The concrete queue implementation is deferred until use cases require work that should outlive
an HTTP request.
"""
