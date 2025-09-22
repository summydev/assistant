import os
import pytz
from typing import Optional

class Config:
    """Application configuration with validation"""
    def __init__(self):
        self.SPITCH_API_KEY = os.getenv("SPITCH_API_KEY")
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") 
        self.OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
        self.MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
        self.MAX_AUDIO_DURATION = 300  # 5 minutes
        self.SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours
        self.RATE_LIMIT = 100  # requests per hour
        self.DB_PATH = "agent_memory.db"
        self.AUDIO_DIR = "temp_audio"
        self.TIMEZONE = pytz.timezone("Africa/Lagos")
        
    def validate(self):
        """Validate configuration at startup"""
        errors = []
        if not self.SPITCH_API_KEY:
            errors.append("SPITCH_API_KEY environment variable required")
        if not self.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY environment variable required")
        
        if errors:
            raise ValueError("Configuration errors: " + ", ".join(errors))

config = Config()