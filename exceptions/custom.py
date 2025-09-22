class AgentError(Exception):
    """Base exception for agent errors"""
    pass

class TranscriptionError(AgentError):
    """Speech transcription failed"""
    pass

class IntentAnalysisError(AgentError):
    """Intent analysis failed"""
    pass

class TaskExecutionError(AgentError):
    """Task execution failed"""
    pass

class DatabaseError(AgentError):
    """Database operation failed"""
    pass