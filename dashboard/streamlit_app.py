"""
Entry point for Streamlit Community Cloud.

Lives in its own folder so Cloud installs dashboard/requirements.txt (the
few packages the dashboard needs) instead of the root requirements.txt,
which pulls in torch, spaCy and sentence-transformers for the pipeline.
"""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(__file__), "..", "src", "dashboard.py"),
               run_name="__main__")
