from pydantic import BaseModel, validator, Field
from typing import Optional, List, Dict, Any
from .enums import Intent

class AgentResponse(BaseModel):
    intent: Intent
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    details: Dict[str, Any] = Field(default_factory=dict)
    follow_up_questions: List[str] = Field(default_factory=list)
    context_updates: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('confidence')
    def validate_confidence(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError('Confidence must be between 0.0 and 1.0')
        return v

class TaskExecution(BaseModel):
    task_id: str
    status: str = Field(..., regex=r"^(pending|in_progress|completed|failed)$")  # Fixed: using regex instead of pattern
    result: Dict[str, Any] = Field(default_factory=dict)
    execution_time: float = Field(..., ge=0.0)
    follow_up_actions: List[Dict[str, Any]] = Field(default_factory=list)

class VoiceAgentResponse(BaseModel):
    transcription: str
    original_language: str
    agent_decision: AgentResponse
    task_execution: TaskExecution
    response_text: str
    response_audio_url: Optional[str] = None
    context_updates: Dict[str, Any] = Field(default_factory=dict)
    success: bool
    session_id: str