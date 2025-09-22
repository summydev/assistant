import re
import pytz
from datetime import datetime, timedelta
import dateparser
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

class SmartTimeParser:
    def __init__(self, default_timezone: str = "Africa/Lagos"):
        self.default_timezone = default_timezone
        self.timezone = pytz.timezone(default_timezone)
        
        # Time patterns for better matching
        self.time_patterns = {
            # Basic time formats
            r'\b(\d{1,2}):(\d{2})\s*(am|pm)?\b': self._parse_time_format,
            r'\b(\d{1,2})\s*(am|pm)\b': self._parse_hour_ampm,
            r'\b(\d{1,2})\s*o\'?clock\b': self._parse_oclock,
            
            # Relative times
            r'\bin\s*(\d+)\s*(minute|hour|day|week)s?\b': self._parse_relative_time,
            r'\bafter\s*(\d+)\s*(minute|hour|day)s?\b': self._parse_relative_time,
            r'\bnext\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b': self._parse_next_weekday,
            r'\bthis\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b': self._parse_this_weekday,
            r'\btomorrow\s*(?:at\s*)?(\d{1,2}):?(\d{2})?\s*(am|pm)?\b': self._parse_tomorrow_time,
            r'\btoday\s*(?:at\s*)?(\d{1,2}):?(\d{2})?\s*(am|pm)?\b': self._parse_today_time,
            
            # Natural language times
            r'\b(morning|afternoon|evening|night)\b': self._parse_time_of_day,
            r'\bnoon\b': lambda m: self._get_base_date().replace(hour=12, minute=0),
            r'\bmidnight\b': lambda m: self._get_base_date().replace(hour=0, minute=0) + timedelta(days=1),
            
            # Lunch/dinner times
            r'\blunch\s*time\b': lambda m: self._get_base_date().replace(hour=12, minute=30),
            r'\bdinner\s*time\b': lambda m: self._get_base_date().replace(hour=19, minute=0),
            r'\bbreakfast\s*time\b': lambda m: self._get_base_date().replace(hour=8, minute=0),
        }
        
        # Common time synonyms
        self.time_synonyms = {
            'mins': 'minutes',
            'min': 'minutes',
            'hrs': 'hours',
            'hr': 'hours',
            'h': 'hours',
            'm': 'minutes',
            'wk': 'week',
            'wks': 'weeks',
        }
        
        # Day mappings
        self.weekdays = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
            'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6
        }
    
    def _get_base_date(self) -> datetime:
        """Get current date in the configured timezone"""
        return datetime.now(self.timezone)
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for better parsing"""
        text = text.lower().strip()
        
        # Replace synonyms
        for synonym, replacement in self.time_synonyms.items():
            text = re.sub(f r'\b{synonym}\b', replacement, text) # type: ignore
        
        # Clean up common phrases
        replacements = {
            r'\b(?:by|around|approximately|about)\s+': '',
            r'\b(?:at|on)\s+': '',
            r'\b(?:the)\s+': '',
            r'\s+': ' ',
        }
        
        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text)
        
        return text.strip()
    
    def _parse_time_format(self, match) -> datetime:
        """Parse time in HH:MM format"""
        hour = int(match.group(1))
        minute = int(match.group(2))
        ampm = match.group(3)
        
        if ampm:
            if ampm.lower() == 'pm' and hour != 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0
        
        base_date = self._get_base_date()
        target_time = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If the time has passed today, schedule for tomorrow
        if target_time <= base_date:
            target_time += timedelta(days=1)
        
        return target_time
    
    def _parse_hour_ampm(self, match) -> datetime:
        """Parse hour with AM/PM"""
        hour = int(match.group(1))
        ampm = match.group(2).lower()
        
        if ampm == 'pm' and hour != 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        base_date = self._get_base_date()
        target_time = base_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        
        if target_time <= base_date:
            target_time += timedelta(days=1)
        
        return target_time
    
    def _parse_oclock(self, match) -> datetime:
        """Parse o'clock format"""
        hour = int(match.group(1))
        base_date = self._get_base_date()
        
        # Assume PM if it's a reasonable afternoon/evening time
        if 1 <= hour <= 11 and base_date.hour >= 12:
            hour += 12
        
        target_time = base_date.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
        
        if target_time <= base_date:
            target_time += timedelta(days=1)
        
        return target_time
    
    def _parse_relative_time(self, match) -> datetime:
        """Parse relative time like 'in 2 hours'"""
        amount = int(match.group(1))
        unit = match.group(2).lower()
        
        base_date = self._get_base_date()
        
        if unit.startswith('minute'):
            return base_date + timedelta(minutes=amount)
        elif unit.startswith('hour'):
            return base_date + timedelta(hours=amount)
        elif unit.startswith('day'):
            return base_date + timedelta(days=amount)
        elif unit.startswith('week'):
            return base_date + timedelta(weeks=amount)
        
        return base_date
    
    def _parse_next_weekday(self, match) -> datetime:
        """Parse 'next Monday' etc."""
        weekday_name = match.group(1).lower()
        target_weekday = self.weekdays.get(weekday_name, 0)
        
        base_date = self._get_base_date()
        current_weekday = base_date.weekday()
        
        # Calculate days until next occurrence
        days_ahead = target_weekday - current_weekday
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        
        target_date = base_date + timedelta(days=days_ahead)
        # Set to a reasonable default time (2 PM)
        return target_date.replace(hour=14, minute=0, second=0, microsecond=0)
    
    def _parse_this_weekday(self, match) -> datetime:
        """Parse 'this Monday' etc."""
        weekday_name = match.group(1).lower()
        target_weekday = self.weekdays.get(weekday_name, 0)
        
        base_date = self._get_base_date()
        current_weekday = base_date.weekday()
        
        # Calculate days until this week's occurrence
        days_ahead = target_weekday - current_weekday
        if days_ahead < 0:  # Already passed this week, go to next week
            days_ahead += 7
        
        target_date = base_date + timedelta(days=days_ahead)
        return target_date.replace(hour=14, minute=0, second=0, microsecond=0)
    
    def _parse_tomorrow_time(self, match) -> datetime:
        """Parse 'tomorrow at 3pm'"""
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        
        if ampm:
            if ampm.lower() == 'pm' and hour != 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0
        
        base_date = self._get_base_date()
        tomorrow = base_date + timedelta(days=1)
        return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    def _parse_today_time(self, match) -> datetime:
        """Parse 'today at 3pm'"""
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        
        if ampm:
            if ampm.lower() == 'pm' and hour != 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0
        
        base_date = self._get_base_date()
        target_time = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If time has passed, assume tomorrow
        if target_time <= base_date:
            target_time += timedelta(days=1)
        
        return target_time
    
    def _parse_time_of_day(self, match) -> datetime:
        """Parse general time of day"""
        time_of_day = match.group(1).lower()
        base_date = self._get_base_date()
        
        time_mappings = {
            'morning': 9,
            'afternoon': 14,
            'evening': 18,
            'night': 20
        }
        
        hour = time_mappings.get(time_of_day, 14)
        target_time = base_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        
        # If time has passed today, schedule for tomorrow
        if target_time <= base_date:
            target_time += timedelta(days=1)
        
        return target_time
    
    def parse_datetime(self, text: str) -> Optional[datetime]:
        """
        Main parsing method that tries multiple approaches
        """
        if not text or not text.strip():
            return None
        
        original_text = text
        text = self._normalize_text(text)
        
        logger.info(f"Parsing time: '{original_text}' -> normalized: '{text}'")
        
        # Try custom patterns first
        for pattern, parser in self.time_patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    result = parser(match) if callable(parser) else parser
                    if result:
                        logger.info(f"Parsed with pattern '{pattern}': {result}")
                        return result
                except Exception as e:
                    logger.warning(f"Pattern {pattern} failed: {e}")
                    continue
        
        # Fallback to dateparser with enhanced settings
        try:
            base_date = self._get_base_date()
            
            parsed_dt = dateparser.parse(
                text,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'PREFER_DAY_OF_MONTH': 'first',
                    'RELATIVE_BASE': base_date,
                    'TIMEZONE': self.default_timezone,
                    'RETURN_AS_TIMEZONE_AWARE': True,
                    'DATE_ORDER': 'DMY',
                    'STRICT_PARSING': False,
                    'NORMALIZE': True,
                    'PARSERS': ['timestamp', 'relative-time', 'absolute-time', 'base-formats']
                }
            )
            
            if parsed_dt:
                # Ensure timezone awareness
                if parsed_dt.tzinfo is None:
                    parsed_dt = self.timezone.localize(parsed_dt)
                
                # If parsed time is in the past, try to make it future
                if parsed_dt <= base_date:
                    # If it's just a time today, move to tomorrow
                    if parsed_dt.date() == base_date.date():
                        parsed_dt += timedelta(days=1)
                    # If it's a past date, try to find the next occurrence
                    elif (base_date - parsed_dt).days < 7:
                        parsed_dt += timedelta(days=7)
                
                logger.info(f"Dateparser success: '{text}' -> {parsed_dt}")
                return parsed_dt
            
        except Exception as e:
            logger.error(f"Dateparser failed for '{text}': {e}")
        
        # Last resort: try to extract any numbers and make educated guesses
        return self._intelligent_fallback(text)
    
    def _intelligent_fallback(self, text: str) -> Optional[datetime]:
        """
        Intelligent fallback for when other methods fail
        """
        try:
            base_date = self._get_base_date()
            
            # Look for any time-like patterns
            number_matches = re.findall(r'\d+', text)
            if not number_matches:
                return None
            
            # If we find a single number, make educated guesses
            if len(number_matches) == 1:
                num = int(number_matches[0])
                
                # If it's a reasonable hour (1-24)
                if 1 <= num <= 24:
                    hour = num
                    if hour <= 12:
                        # Assume PM if it's afternoon/evening or if morning time has passed
                        if base_date.hour >= 12 or (base_date.hour > hour):
                            hour = hour if hour == 12 else hour + 12
                    
                    target_time = base_date.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
                    if target_time <= base_date:
                        target_time += timedelta(days=1)
                    
                    logger.info(f"Fallback hour parsing: {num} -> {target_time}")
                    return target_time
                
                # If it's minutes from now
                elif num <= 180:  # Up to 3 hours
                    result = base_date + timedelta(minutes=num)
                    logger.info(f"Fallback minutes parsing: {num} -> {result}")
                    return result
            
            # If we find two numbers, assume it's hour:minute
            elif len(number_matches) == 2:
                hour, minute = int(number_matches[0]), int(number_matches[1])
                if 0 <= hour <= 24 and 0 <= minute <= 59:
                    # Smart AM/PM detection
                    if hour <= 12 and ('pm' in text.lower() or base_date.hour >= 12):
                        hour = hour if hour == 12 else hour + 12
                    
                    target_time = base_date.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
                    if target_time <= base_date:
                        target_time += timedelta(days=1)
                    
                    logger.info(f"Fallback hour:minute parsing: {hour}:{minute} -> {target_time}")
                    return target_time
            
        except Exception as e:
            logger.error(f"Intelligent fallback failed: {e}")
        
        return None
    
    def format_readable_time(self, dt: datetime) -> str:
        """Format datetime for user-friendly display"""
        try:
            if dt.date() == datetime.now(self.timezone).date():
                return f"today at {dt.strftime('%I:%M %p')}"
            elif dt.date() == (datetime.now(self.timezone) + timedelta(days=1)).date():
                return f"tomorrow at {dt.strftime('%I:%M %p')}"
            elif (dt - datetime.now(self.timezone)).days < 7:
                return f"{dt.strftime('%A')} at {dt.strftime('%I:%M %p')}"
            else:
                return dt.strftime('%A, %B %d at %I:%M %p')
        except:
            return dt.strftime('%Y-%m-%d %H:%M')

# Global parser instance
time_parser = SmartTimeParser()

def parse_datetime_smart(datetime_str: str, user_timezone: str = "Africa/Lagos") -> Optional[datetime]:
    """
    Enhanced datetime parsing function to replace the existing one
    """
    global time_parser
    if time_parser.default_timezone != user_timezone:
        time_parser = SmartTimeParser(user_timezone)
    
    return time_parser.parse_datetime(datetime_str)

def format_time_for_user(dt: datetime, user_timezone: str = "Africa/Lagos") -> str:
    """
    Format time in a user-friendly way
    """
    global time_parser
    if time_parser.default_timezone != user_timezone:
        time_parser = SmartTimeParser(user_timezone)
    
    return time_parser.format_readable_time(dt)