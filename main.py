# --- IMPORTS ---
import os
import json
import base64
import re
from io import BytesIO
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, File, UploadFile, Form, Header, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from rapidfuzz import fuzz, process
import io
from pydub import AudioSegment

import google.generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import dateparser
import ast
import operator
import logging
import pytz

# --- SPITCH IMPORT ---
from spitch import Spitch

# --- CONFIGURATIONS ---
# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

# Initialize Spitch client
try:
    spitch_client = Spitch(api_key=os.getenv("SPITCH_API_KEY"))
    logger.info("Spitch client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Spitch client: {e}")
    spitch_client = None

app = FastAPI(title="Voice Assistant API", description="Multi-language voice assistant with Spitch integration")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_HISTORY_TURNS = 6
TASK_CONFIDENCE_THRESHOLD = 0.7
DISALLOWED_TOPICS = ["self-harm", "violence", "hate", "illegal"]

# List of supported languages for transcription
SUPPORTED_TRANSCRIPTION_LANGUAGES = ["en", "ha", "yo", "ig", "am"]

# Spitch TTS language support and CORRECTED voice mapping
SPITCH_TTS_LANGUAGES = {
    "en": "lina",     # English - CORRECTED: Use English voices
    "ha": "hasan",    # Hausa  
    "ig": "obinna",   # Igbo
    "am": "tesfaye",  # Amharic
    "yo": "sade"      # Yoruba
}

# Available Spitch voices
SPITCH_VOICES = ["sade", "funmi", "kemi", "tolu", "bola", "emeka", "ada", "chidi"]

# Relationship mappings for better contact matching
RELATIONSHIP_MAPPINGS = {
    'en': {
        'mom': ['mother', 'mum', 'ma', 'mama', 'mommy'],
        'dad': ['father', 'pa', 'papa', 'daddy'],
        'wife': ['spouse', 'partner'],
        'husband': ['spouse', 'partner'],
        'brother': ['bro'],
        'sister': ['sis'],
        'friend': ['buddy', 'pal', 'mate'],
    },
    'yo': {
        'mama': ['iya', 'mother', 'mom'],
        'baba': ['father', 'dad'],
        'ore': ['friend'],
    },
    'ha': {
        'mama': ['uwa', 'mother', 'mom'],
        'baba': ['father', 'dad'],
        'aboki': ['friend'],
    }
}

# --- DATA CLASSES ---
class UserText(BaseModel):
    text: str
    contacts: Optional[List[Dict[str, str]]] = []
    language: Optional[str] = "en"

class TTSRequest(BaseModel):
    text: str
    language: str = "en"
    voice: Optional[str] = None

# --- SESSION STORE (per user, per day) ---
user_sessions: Dict[str, Dict[str, Dict]] = {}

def get_today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def get_user_session(user_id: str) -> Dict:
    today = get_today_key()
    if user_id not in user_sessions:
        user_sessions[user_id] = {}
    if today not in user_sessions[user_id]:
        user_sessions[user_id][today] = {
            "history": [],
            "awaiting_info": None,
            "task_context": None,
            "language": "en"
        }
    return user_sessions[user_id][today]

# --- UTILITIES ---
def clean_phone_number(phone: str) -> str:
    """Clean phone number by removing all non-digit characters except +"""
    if not phone:
        return ""
    # Keep only digits and + sign
    cleaned = re.sub(r'[^0-9+]', '', phone)
    # If it starts with 0, convert to international format for Nigeria
    if cleaned.startswith('0') and len(cleaned) == 11:
        cleaned = '+234' + cleaned[1:]
    return cleaned

def extract_name_from_phrase(text: str) -> str:
    """
    Extract a name from phrases like:
    - "a friend named Zizi"
    - "call my friend Bola"
    - "please call Zizi"
    """
    # Patterns to look for
    patterns = [
        r'(?:friend|person|contact|someone)\s+(?:named|called|by\s+the\s+name\s+of)\s+([a-zA-Z0-9\s]+)',
        r'(?:my|the)\s+(?:friend|contact)\s+([a-zA-Z0-9\s]+)',
        r'call\s+(?:my|the)?\s*([a-zA-Z0-9\s]+)',
        r'call\s+(?:to|for)\s+([a-zA-Z0-9\s]+)',
    ]
    
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            name = match.group(1).strip()
            # Remove any remaining connecting words
            name = re.sub(r'^(my|the|a|an)\s+', '', name)
            return name
    
    # If no pattern matched, try to extract the last word as name
    words = text_lower.split()
    if len(words) > 1:
        # Skip common verbs and prepositions
        skip_words = {'call', 'to', 'for', 'my', 'the', 'a', 'an', 'please', 'could', 'would', 'can'}
        for word in reversed(words):
            if word not in skip_words:
                return word
    
    return text  # Fallback to returning the original text

def find_best_contact_match(contact_name: str, contacts: List[Dict[str, str]], language: str = "en") -> Optional[Dict[str, str]]:
    if not contacts:
        logger.warning("No contacts provided for matching")
        return None
    
    # Extract name from phrases like "a friend named Zizi"
    original_name = contact_name
    contact_name = extract_name_from_phrase(contact_name)
    
    # Normalize the search term
    contact_name = contact_name.lower().strip()
    logger.info(f"Searching for: '{contact_name}' (extracted from: '{original_name}') in {len(contacts)} contacts")
    
    # Expand search terms based on relationships
    search_terms = [contact_name]
    if contact_name in RELATIONSHIP_MAPPINGS.get(language, {}):
        search_terms.extend(RELATIONSHIP_MAPPINGS[language][contact_name])
    elif contact_name in RELATIONSHIP_MAPPINGS.get('en', {}):
        search_terms.extend(RELATIONSHIP_MAPPINGS['en'][contact_name])
    
    best_match = None
    best_score = 0
    
    for contact in contacts:
        name = contact.get('name', '').lower()
        if not name:
            continue
            
        # Clean phone number
        if 'phone' in contact:
            contact['phone'] = clean_phone_number(contact['phone'])
            
        # Check all search terms
        for term in search_terms:
            # Strategy 1: Exact match
            if name == term:
                logger.info(f"Exact match found: {name} for {term}")
                return contact
                
            # Strategy 2: Contains match (either way)
            if term in name or name in term:
                score = 85
                logger.info(f"Contains match: {name} for {term}")
                if score > best_score:
                    best_score = score
                    best_match = contact
                    continue
                    
            # Strategy 3: Fuzzy matching
            score = fuzz.ratio(term, name)
            logger.info(f"Fuzzy match: {name} for {term} - score: {score}")
            
            # Strategy 4: First name match
            first_name = name.split()[0] if name.split() else ""
            if first_name == term:
                score = max(score, 90)  # Prioritize first name matches
                logger.info(f"First name match: {first_name} for {term}")
            
            # Update best match if this is better
            if score > best_score and score > 40:  # Lowered threshold to 40
                best_score = score
                best_match = contact
            
    if best_match:
        logger.info(f"Best match: {best_match.get('name')} with score {best_score}")
    else:
        logger.warning(f"No match found for: {contact_name}")
        # Try one more approach: search in phone numbers for partial matches
        for contact in contacts:
            phone = contact.get('phone', '')
            if contact_name in phone:
                logger.info(f"Found partial match in phonenumber: {contact.get('name')}")
                return contact
        
    return best_match

def get_calendar_service():
    try:
        if not os.path.exists("token.json"):
            logger.error("Google OAuth token.json not found.")
            return None
            
        creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/calendar"])
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.error(f"Error creating calendar service: {e}")
        return None

def create_google_calendar_event(contact_name: str, start_datetime_str: str, duration_minutes: int = 60):
    try:
        service = get_calendar_service()
        if not service:
            return None
            
        # Parse the datetime string with timezone awareness
        if 'Z' in start_datetime_str:
            start_dt = datetime.fromisoformat(start_datetime_str.replace('Z', '+00:00'))
        else:
            start_dt = datetime.fromisoformat(start_datetime_str)
            
        # Ensure timezone awareness
        if start_dt.tzinfo is None:
            # Assume Africa/Lagos timezone if no timezone info
            lagos_tz = pytz.timezone('Africa/Lagos')
            start_dt = lagos_tz.localize(start_dt)
            
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        
        event = {
            'summary': f'Meeting with {contact_name}',
            'description': f'Meeting scheduled through voice assistant',
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'Africa/Lagos',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'Africa/Lagos',
            },
            'reminders': {
                'useDefault': True,
            },
        }
        
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return created_event.get('htmlLink')
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        return None

def create_reminder_event(title: str, start_datetime_str: str):
    try:
        service = get_calendar_service()
        if not service:
            return None
            
        # Parse the datetime string with timezone awareness
        if 'Z' in start_datetime_str:
            start_dt = datetime.fromisoformat(start_datetime_str.replace('Z', '+00:00'))
        else:
            start_dt = datetime.fromisoformat(start_datetime_str)
            
        # Ensure timezone awareness
        if start_dt.tzinfo is None:
            # Assume Africa/Lagos timezone if no timezone info
            lagos_tz = pytz.timezone('Africa/Lagos')
            start_dt = lagos_tz.localize(start_dt)
            
        event = {
            'summary': f'Reminder: {title}',
            'description': f'Reminder set through voice assistant',
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'Africa/Lagos',
            },
            'end': {
                'dateTime': (start_dt + timedelta(minutes=15)).isoformat(),
                'timeZone': 'Africa/Lagos',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 30},
                    {'method': 'email', 'minutes': 60},
                ],
            },
        }
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return created_event.get('htmlLink')
    except Exception as e:
        logger.error(f"Error creating reminder event: {e}")
        return None

def clean_json_output(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    return raw

def text_to_speech_spitch(text: str, lang: str = "en", voice: Optional[str] = None) -> str:
    """
    Convert text to speech using Spitch TTS API and convert to MP3 for better compatibility
    """
    try:
        if not spitch_client:
            logger.error("Spitch client not initialized")
            return ""
            
        # Clean the text for better speech generation
        safe_text = re.sub(r'http\S+', '', text).strip()
        if "Here's the meeting link" in text or "Here's the link" in text:
            safe_text += " I've shared the link with you."
        
        # Use default voice for language if not specified
        if not voice:
            voice = SPITCH_TTS_LANGUAGES.get(lang, "lina")
        
        # Check if language is supported
        if lang not in SPITCH_TTS_LANGUAGES:
            logger.warning(f"Language '{lang}' not supported by Spitch TTS, falling back to English")
            lang = "en"
            voice = "lina"
        
        logger.info(f"Generating speech with Spitch - Lang: {lang}, Voice: {voice}, Text: {safe_text[:50]}...")
        
        # Generate speech using Spitch
        response = spitch_client.speech.generate(
            text=safe_text,
            language=lang,
            voice=voice
        )
        
        # Read the audio content
        audio_content = response.read()
        
        # Convert WAV to MP3 for better compatibility with Android
        try:
            # Convert WAV to MP3 using pydub
            audio_segment = AudioSegment.from_wav(io.BytesIO(audio_content))
            mp3_buffer = io.BytesIO()
            audio_segment.export(mp3_buffer, format="mp3")
            mp3_buffer.seek(0)
            audio_content = mp3_buffer.read()
            logger.info("Converted WAV to MP3 for better compatibility")
        except Exception as conversion_error:
            logger.error(f"Failed to convert WAV to MP3: {conversion_error}")
            # Fall back to original WAV if conversion fails
        
        # Encode to base64
        audio_base64 = base64.b64encode(audio_content).decode("utf-8")
        
        logger.info(f"Spitch TTS successful - Generated {len(audio_content)} bytes of audio")
        return audio_base64
        
    except Exception as e:
        logger.error(f"Spitch TTS failed: {e}")
        return ""
        
def text_to_speech(text: str, lang: str = "en") -> str:
    """
    Wrapper function that uses Spitch TTS
    """
    return text_to_speech_spitch(text, lang)

def is_disallowed_content(text: str) -> bool:
    return any(topic in text.lower() for topic in DISALLOWED_TOPICS)

# --- TRANSLATION FUNCTION ---
def translate_text(text: str, source_lang: str, target_lang: str = "en") -> str:
    """
    Translate text using Spitch's translation API
    """
    try:
        if not spitch_client:
            logger.error("Spitch client not initialized")
            return text
            
        logger.info(f"Attempting translation from {source_lang} to {target_lang}")
        translation = spitch_client.text.translate(
            text=text,
            source=source_lang,
            target=target_lang
        )
        logger.info(f"Translation successful: {translation.text}")
        return translation.text
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return text  # Return original text if translation fails

# --- TONE MARKING FUNCTION ---
def apply_tone_marking(text: str, language: str) -> str:
    """
    Apply tone marking using Spitch API for better pronunciation
    """
    try:
        if not spitch_client:
            logger.error("Spitch client not initialized")
            return text
            
        if language not in SPITCH_TTS_LANGUAGES:
            return text
        
        logger.info(f"Applying tone marking for language: {language}")
        tone_response = spitch_client.text.tone_mark(
            text=text,
            language=language
        )
        logger.info(f"Tone marking successful: {tone_response.text}")
        return tone_response.text
    except Exception as e:
        logger.error(f"Tone marking failed: {e}")
        return text  # Return original text if tone marking fails

# --- TASK ANALYSIS ---
def analyze_user_intent(user_text: str, contacts: List[Dict[str, str]], context: Dict) -> Dict[str, Any]:
    logger.info(f"Received contacts: {contacts}")
    
    contact_names = [c['name'] for c in contacts] if contacts else []
    contact_info = f"Available contacts: {', '.join(contact_names[:10])}" if contact_names else "No contacts available."
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    system_prompt = f"""
You are an intelligent task analysis agent for a voice assistant.

Current time: {current_time}
{contact_info}

Analyze the user input and determine the task type.

For calling or messaging, if the contact is found in the provided contacts list, 
you don't need to ask for phone number as it's already available.

For scheduling meetings, you can use any name provided by the user - it doesn't need to match contacts.
Only datetime is required for scheduling meetings.

For setting reminders, only the reminder text and datetime are required.

When extracting contact names from phrases like "a friend named Zizi" or "call my friend Bola",
try to extract just the name part (e.g., "Zizi", "Bola").

Respond ONLY with valid JSON:

{{
  "is_task": boolean,
  "task_type": "call_person | send_sms | schedule_meeting | set_alarm | set_reminder | weather | note_taking | calculator | timer | unknown",
  "confidence": float,
  "action_required": boolean,
  "parameters": {{
    "contact_name": "",
    "phone_number": "", 
    "message": "",
    "datetime": "",
    "alarm_time": "",
    "reminder_text": "",
    "meeting_title": "",
    "location": "",
    "calculation": "",
    "timer_duration": "",
    "note_content": ""
  }},
  "missing_info": [],
  "suggested_response": "",
  "explanation": ""
}}

Conversation so far:
{context['history'][-10:]}

User input: "{user_text}"
"""
    try:
        response = model.generate_content(system_prompt, generation_config={"temperature": 0.3, "max_output_tokens": 300})
        cleaned = clean_json_output(response.text)
        result = json.loads(cleaned)
        logger.info(f"Task analysis result: {result}")
        
        # Check if we have the contact and phone number for call/sms tasks
        if result.get("task_type") in ["call_person", "send_sms"]:
            contact_name = result.get("parameters", {}).get("contact_name")
            if contact_name:
                matched_contact = find_best_contact_match(contact_name, contacts, context.get("language", "en"))
                if matched_contact and matched_contact.get('phone'):
                    # We have the contact and phone number, so remove phone_number from missing_info
                    if "phone_number" in result.get("missing_info", []):
                        result["missing_info"].remove("phone_number")
                        logger.info(f"Removed phone_number from missing_info for {contact_name}")
        
        # Fix for meeting scheduling - don't require phone number
        if result.get("task_type") == "schedule_meeting" and "phone_number" in result.get("missing_info", []):
            result["missing_info"].remove("phone_number")
            logger.info("Removed phone_number from missing_info for meeting scheduling")
            
        return result
    except Exception as e:
        logger.error(f"Task analysis failed: {e}")
        return {
            "is_task": False,
            "task_type": "unknown", 
            "confidence": 0.0,
            "action_required": False,
            "parameters": {},
            "missing_info": [],
            "suggested_response": "I didn't understand that. Could you please repeat?",
            "explanation": "Failed to analyze input"
        }

# --- TASK EXECUTION ---
def execute_task(task_analysis: Dict, contacts: List[Dict[str, str]], context: Dict, user_text: str) -> tuple:
    task_type = task_analysis.get("task_type")
    params = task_analysis.get("parameters", {})
    missing = task_analysis.get("missing_info", [])

    # Filter out phone_number from missing_info if it's a meeting
    if task_type == "schedule_meeting" and "phone_number" in missing:
        missing.remove("phone_number")

    if missing and not context.get("awaiting_info"):
        context["awaiting_info"] = missing[0]
        context["task_context"] = task_analysis
        return task_analysis.get("suggested_response", "I need more information."), context, None, None, None

    if context.get("awaiting_info"):
        expected = context["awaiting_info"]
        if expected == "contact_name" and any(c['name'].lower() in user_text.lower() for c in contacts):
            for contact in contacts:
                if contact['name'].lower() in user_text.lower():
                    params['contact_name'] = contact['name']
                    break
        elif expected == "location":
            params['location'] = user_text.strip()
        elif expected in ["datetime", "alarm_time"]:
            params['datetime'] = user_text.strip()
            params['alarm_time'] = user_text.strip()
        elif expected == "reminder_text":
            params['reminder_text'] = user_text.strip()

        context["awaiting_info"] = None
        if context.get("task_context"):
            task_analysis = context["task_context"]
            context.pop("task_context")

    meeting_link = None
    action = None
    phone_number = None
    reply = "I'm not sure how to help with that yet."

    if task_type == "call_person":
        contact_name = params.get("contact_name")
        if not contact_name:
            return "Who would you like to call?", context, None, None, None

        matched_contact = find_best_contact_match(contact_name, contacts, context.get("language", "en"))
        logger.info(f"Matched contact: {matched_contact}")
        if matched_contact:
            phone_number = matched_contact.get('phone')
            if phone_number:
                action = "make_call"
                reply = f"Calling {matched_contact['name']} now."
                
                # Return phone number in a format the frontend expects
                return reply, context, None, action, {"number": phone_number, "name": matched_contact['name']}
            else:
                reply = f"I found {matched_contact['name']} but couldn't find a phone number."
        else:
            available = [c['name'] for c in contacts[:5]]
            reply = f"I couldn't find '{contact_name}'. Available contacts: {', '.join(available)}"

    elif task_type == "send_sms":
        contact_name = params.get("contact_name")
        message = params.get("message")
        if not contact_name:
            return "Who should I send the message to?", context, None, None, None
        if not message:
            return "What message would you like to send?", context, None, None, None
        matched_contact = find_best_contact_match(contact_name, contacts, context.get("language", "en"))
        if matched_contact:
            action = "send_sms"
            phone_number = matched_contact.get('phone')
            reply = f"Message sent to {matched_contact['name']}: {message}"
        else:
            reply = f"I couldn't find '{contact_name}' in your contacts."

    elif task_type == "set_alarm":
        datetime_str = params.get("datetime") or params.get("alarm_time")
        if not datetime_str:
            return "When should I set the alarm?", context, None, None, None
        parsed_time = dateparser.parse(datetime_str)
        if parsed_time:
            logger.info(f"Parsed alarm time: {parsed_time.isoformat()}")
            reply = f"Alarm set for {parsed_time.strftime('%A at %I:%M %p')}."
            action = "set_alarm"
            return reply, context, None, "set_alarm", {"time": parsed_time.isoformat()}
        else:
            reply = "I couldn't understand the alarm time. Could you repeat?"

    elif task_type == "set_reminder":
        datetime_str = params.get("datetime")
        reminder_text = params.get("reminder_text", "Reminder")
        if not datetime_str:
            return "When should I set the reminder?", context, None, None, None
        
        # Improved date parsing with better error handling
        try:
            # Preprocess the datetime string to handle common phrases
            datetime_str = datetime_str.replace("by ", "at ").replace("around ", "at ")
            
            # Parse with specific settings for better results
            parsed_time = dateparser.parse(datetime_str, settings={
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': datetime.now(),
                'PREFER_DAY_OF_MONTH': 'first',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'TIMEZONE': 'Africa/Lagos'
            })
            
            if parsed_time:
                # Ensure the time is in the future
                current_time = datetime.now(pytz.timezone('Africa/Lagos'))
                if parsed_time < current_time:
                    # If it's in the past, assume they meant tomorrow
                    parsed_time = parsed_time + timedelta(days=1)
                    logger.info(f"Adjusted time to future: {parsed_time}")
                    
                meeting_link = create_reminder_event(reminder_text, parsed_time.isoformat())
                if meeting_link:
                    reply = f"Reminder set for {parsed_time.strftime('%A at %I:%M %p')}: {reminder_text}"
                    action = "set_reminder"
                else:
                    reply = "I couldn't create the reminder. Please check your Google Calendar connection."
            else:
                # Try to give more specific guidance based on the input
                if "tomorrow" in datetime_str.lower():
                    reply = "I understand you want to set a reminder for tomorrow. What time tomorrow?"
                elif "next week" in datetime_str.lower():
                    reply = "I understand you want to set a reminder for next week. Which day and time?"
                else:
                    reply = "I need a specific date and time for the reminder. For example, 'tomorrow at 3pm' or 'Friday at 10am'."
        except Exception as e:
            logger.error(f"Error parsing time for reminder: {e}")
            reply = "I need a specific date and time for the reminder. For example, 'tomorrow at 3pm' or 'Friday at 10am'."

    elif task_type == "schedule_meeting":
        contact_name = params.get("contact_name", "Someone")
        datetime_str = params.get("datetime")
        if not datetime_str:
            return "When should the meeting be scheduled?", context, None, None, None
        
        # Improved date parsing with better error handling
        try:
            # Preprocess the datetime string to handle common phrases
            datetime_str = datetime_str.replace("by ", "at ").replace("around ", "at ")
            
            # Parse with specific settings for better results
            parsed_time = dateparser.parse(datetime_str, settings={
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': datetime.now(),
                'PREFER_DAY_OF_MONTH': 'first',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'TIMEZONE': 'Africa/Lagos'
            })
            
            if parsed_time:
                # Ensure the time is in the future
                current_time = datetime.now(pytz.timezone('Africa/Lagos'))
                if parsed_time < current_time:
                    # If it's in the past, assume they meant tomorrow
                    parsed_time = parsed_time + timedelta(days=1)
                    logger.info(f"Adjusted time to future: {parsed_time}")
                    
                meeting_link = create_google_calendar_event(contact_name, parsed_time.isoformat())
                if meeting_link:
                    if contact_name != "Someone":
                        reply = f"Meeting with {contact_name} scheduled for {parsed_time.strftime('%A at %I:%M %p')}. I've added it to your calendar."
                    else:
                        reply = f"Meeting scheduled for {parsed_time.strftime('%A at %I:%M %p')}. I've added it to your calendar."
                    action = "schedule_meeting"
                else:
                    reply = "I couldn't create the calendar event. Please check your Google Calendar connection."
            else:
                # Try to give more specific guidance based on the input
                if "tomorrow" in datetime_str.lower():
                    reply = "I understand you want to schedule for tomorrow. What time tomorrow?"
                elif "next week" in datetime_str.lower():
                    reply = "I understand you want to schedule for next week. Which day and time?"
                else:
                    reply = "I need a specific date and time. For example, 'tomorrow at 3pm' or 'Friday at 10am'."
        except Exception as e:
            logger.error(f"Error parsing time: {e}")
            reply = "I need a specific date and time. For example, 'tomorrow at 3pm' or 'Friday at 10am'."

    elif task_type == "weather":
        location = params.get("location")
        if not location:
            return "For which location would you like the weather?", context, None, None, None
        reply = f"The weather in {location} is sunny and 28°C."

    elif task_type == "calculator":
        calculation = params.get("calculation", "")
        try:
            calculation_expr = (
                calculation
                .replace('times', '*')
                .replace('plus', '+')
                .replace('minus', '-')
                .replace('divided by', '/')
            )
            node = ast.parse(calculation_expr, mode='eval')
            def safe_eval(n):
                if isinstance(n, ast.Num):
                    return n.n
                elif isinstance(n, ast.BinOp):
                    ops = {
                        ast.Add: operator.add,
                        ast.Sub: operator.sub,
                        ast.Mult: operator.mul,
                        ast.Div: operator.truediv
                    }
                    return ops[type(n.op)](safe_eval(n.left), safe_eval(n.right))
                else:
                    raise ValueError("Unsupported operation")
            result = safe_eval(node.body)
            reply = f"The answer is {result}."
        except Exception:
            reply = "I couldn't solve that calculation. Could you try again?"

    elif task_type == "timer":
        duration = params.get("timer_duration")
        if duration:
            reply = f"Timer set for {duration} minutes."
            action = "set_timer"
        else:
            return "How long should the timer be?", context, None, None, None

    return reply, context, meeting_link, action, phone_number

# --- MAIN ROUTES ---
@app.post("/agent")
async def agent(user_input: UserText, user_id: Optional[str] = Header(None)):
    if not user_id:
        user_id = "default_user"

    context = get_user_session(user_id)
    contacts = user_input.contacts or []
    
    # Clean phone numbers in contacts
    for contact in contacts:
        if 'phone' in contact:
            contact['phone'] = clean_phone_number(contact['phone'])
    
    # Update language preference if provided
    if user_input.language:
        context["language"] = user_input.language

    context["history"].append(f"User: {user_input.text}")

    task_analysis = analyze_user_intent(user_input.text, contacts, context)

    meeting_link = None
    action = None
    phone_number = None

    if task_analysis.get("is_task") and task_analysis.get("confidence", 0) >= TASK_CONFIDENCE_THRESHOLD:
        reply, context, meeting_link, action, phone_number = execute_task(
            task_analysis, contacts, context, user_input.text
        )
    else:
        lower_text = user_input.text.lower()
        if "time" in lower_text:
            now = datetime.now().strftime("%I:%M %p on %A, %B %d")
            reply = f"The current time is {now}."
        elif "date" in lower_text:
            today = datetime.now().strftime("%A, %B %d, %Y")
            reply = f"Today is {today}."
        elif "joke" in lower_text:
            joke_resp = model.generate_content("Tell me a short, clean, funny joke.")
            reply = joke_resp.text.strip() if joke_resp else "I couldn't find a joke right now!"
        elif "motivate" in lower_text or "inspire" in lower_text:
            insp_resp = model.generate_content("Give me one short motivational quote.")
            reply = insp_resp.text.strip() if insp_resp else "Keep going, you're doing great!"
        else:
            history_snippet = "\n".join(context["history"][-6:])
            qa_prompt = f"""
            You are a helpful assistant. Here's recent conversation history:
            {history_snippet}

            Now answer the user's latest input:
            User: {user_input.text}
            Assistant:
            """
            qa_resp = model.generate_content(
                qa_prompt,
                generation_config={"temperature": 0.6, "max_output_tokens": 150}
            )
            reply = qa_resp.text.strip() if qa_resp else "I'm not sure, but I can look that up."

    context["history"].append(f"Assistant: {reply}")
    context["history"] = context["history"][-(MAX_HISTORY_TURNS * 2):]
    user_sessions[user_id][get_today_key()] = context

    # Use Spitch TTS for text-to-speech in the user's preferred language
    user_language = context.get("language", "en")
    tts_audio = text_to_speech(reply, user_language)

    return {
        "analysis": task_analysis,
        "reply": reply,
        "meeting_link": meeting_link,
        "action": action,
        "phone_number": phone_number,
        "tts_audio": tts_audio,
        "language": user_language,
        "audio_format": "base64 encoded wav audio"
    }

# --- TTS TEST ENDPOINT ---
@app.post("/test_tts")
async def test_tts(request: TTSRequest):
    """
    Test endpoint to verify TTS functionality
    """
    try:
        audio_base64 = text_to_speech_spitch(request.text, request.language, request.voice)
        if audio_base64:
            return {
                "status": "success",
                "text": request.text,
                "language": request.language,
                "voice": request.voice or SPITCH_TTS_LANGUAGES.get(request.language, "lina"),
                "audio_length": len(audio_base64),
                "audio_available": True
            }
        else:
            return {
                "status": "error",
                "message": "No audio generated",
                "audio_available": False
            }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "audio_available": False
        }

# --- AUDIO PLAYBACK ENDPOINT ---
@app.post("/play_audio")
async def play_audio(base64_audio: str = Form(...)):
    """
    Decode and return audio file for direct playback
    """
    try:
        audio_data = base64.b64decode(base64_audio)
        return JSONResponse(
            content={
                "status": "success",
                "message": "Audio decoded successfully",
                "audio_size": len(audio_data)
            }
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode audio: {str(e)}")

# --- AUDIO TRANSCRIPTION ---
@app.post("/transcribe")
async def transcribe_audio(
    audio_file: UploadFile = File(...),
    language: str = Form(..., description="Language code (en, ha, yo, ig, am)"),
    user_id: Optional[str] = Header(None)
):
    try:
        logger.info(f"Transcribing audio for language: {language}")
        
        # Read audio file
        audio_content = await audio_file.read()
        logger.info(f"Audio file size: {len(audio_content)} bytes")
        
        # Check file size (Spitch supports up to 25MB)
        if len(audio_content) > 25 * 1024 * 1024:
            return {"error": "File size too large. Maximum size is 25MB."}
        
        # Check file format
        allowed_formats = [".mp3", ".wav", ".m4a", ".ogg", ".flac"]
        if not any(audio_file.filename.lower().endswith(ext) for ext in allowed_formats):
            return {"error": f"Unsupported file format. Supported formats: {', '.join(allowed_formats)}"}
        
        if not spitch_client:
            return {"error": "Spitch client not initialized"}
        
        logger.info(f"Attempting transcription with Spitch for language: {language}")
        
        # Transcribe with Spitch
        response = spitch_client.speech.transcribe(
            language=language,
            content=audio_content
        )
        
        logger.info(f"Transcription successful: {response.text}")
        
        return {
            "status": "success",
            "request_id": response.request_id,
            "text": response.text,
            "language": language,
            "confidence": getattr(response, 'confidence', 1.0)
        }
        
    except Exception as e:
        logger.error(f"Transcription failed: {str(e)}")
        return {"status": "error", "error": f"Transcription failed: {str(e)}"}

# --- PROCESS AUDIO DIRECTLY ---
@app.post("/agent_audio")
async def agent_audio(
    audio_file: UploadFile = File(...),
    language: str = Form(..., description="Language code (en, yo, ha, ig, am)"),
    contacts: Optional[str] = Form(None),
    user_id: Optional[str] = Header(None),
    voice: Optional[str] = Form(None, description="Voice to use for TTS response")
):
    try:
        logger.info(f"Processing audio for language: {language}")
        
        # First transcribe the audio
        transcription_response = await transcribe_audio(audio_file, language, user_id)
        
        if "error" in transcription_response or transcription_response.get("status") == "error":
            error_msg = transcription_response.get("error", "Unknown transcription error")
            logger.error(f"Transcription error: {error_msg}")
            return {"status": "error", "error": f"Transcription failed: {error_msg}"}
        
        transcribed_text = transcription_response["text"]
        original_language = language
        
        logger.info(f"Transcription successful: {transcribed_text}")
        
        # If the language is not English, translate to English
        if language != "en":
            logger.info(f"Translating from {language} to English")
            translated_text = translate_text(transcribed_text, language, "en")
            logger.info(f"Translated text: {transcribed_text} -> {translated_text}")
        else:
            translated_text = transcribed_text
        
        # Parse contacts if provided
        contacts_list = []
        if contacts:
            try:
                contacts_list = json.loads(contacts)
                 # Clean phone numbers in contacts
                for contact in contacts_list:
                    if 'phone' in contact:
                        contact['phone'] = clean_phone_number(contact['phone'])
            except json.JSONDecodeError:
                logger.error("Failed to parse contacts")
                return {"status": "error", "error": "Invalid contacts format"}
        
        # Create UserText object with translated text
        user_input = UserText(text=translated_text, contacts=contacts_list, language=language)
        
        # Process with existing agent logic
        logger.info("Processing with agent")
        result = await agent(user_input, user_id)
        
        # If the original language was not English, translate the response back
        if original_language != "en":
            logger.info(f"Translating response back to {original_language}")
            translated_reply = translate_text(result["reply"], "en", original_language)
            result["reply"] = translated_reply
            
            # Apply tone marking for better pronunciation
            tone_marked_reply = apply_tone_marking(translated_reply, original_language)
            
            # Regenerate TTS in the original language with specified voice
            selected_voice = voice if voice and voice in SPITCH_VOICES else SPITCH_TTS_LANGUAGES.get(original_language, "lina")
            result["tts_audio"] = text_to_speech_spitch(tone_marked_reply, original_language, selected_voice)
        
        # Add the original transcribed text to the response
        result["original_text"] = transcribed_text
        result["original_language"] = original_language
        result["status"] = "success"
        
        logger.info("Audio processing completed successfully")
        return result
        
    except Exception as e:
        logger.error(f"Audio processing failed: {str(e)}")
        return {"status": "error", "error": f"Audio processing failed: {str(e)}"}

# --- DEBUG CONTACTS ENDPOINT ---
@app.post("/debug_contact_matching")
async def debug_contact_matching(
    contact_name: str = Form(...),
    contacts: Optional[str] = Form(None),
    language: str = Form("en")
):
    """Debug endpoint to test contact matching"""
    try:
        contacts_list = json.loads(contacts) if contacts else []
        logger.info(f"Testing contact matching for: '{contact_name}'")
        logger.info(f"Contacts available: {len(contacts_list)}")
        
        match = find_best_contact_match(contact_name, contacts_list, language)
        
        return {
            "search_term": contact_name,
            "contacts_count": len(contacts_list),
            "match_found": match is not None,
            "match_details": match,
            "all_contacts": contacts_list[:10]  # First 10 contacts for debugging
        }
    except Exception as e:
        return {"error": str(e)}

# --- ROOT ENDPOINT ---
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Voice Assistant API",
        "version": "1.0",
        "endpoints": {
            "/agent": "Text-based agent interaction",
            "/agent_audio": "Audio-based agent interaction",
            "/transcribe": "Audio transcription",
            "/test_tts": "Test TTS functionality",
            "/debug_contact_matching": "Debug contact matching",
            "/health": "System health check"
        },
        "supported_languages": SUPPORTED_TRANSCRIPTION_LANGUAGES
    }

# --- HEALTH CHECK ENDPOINT ---
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "spitch_available": spitch_client is not None,
        "gemini_available": True,  # Assuming Gemini is always available if API key is set
        "calendar_available": get_calendar_service() is not None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)