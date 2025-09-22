import 'package:path_provider/path_provider.dart';
import 'package:http_parser/http_parser.dart'; 
import 'dart:async';
import 'dart:convert';
import 'dart:io' show Platform;
import 'dart:io';
import 'package:path_provider/path_provider.dart';

import 'package:flutter/material.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest.dart' as tz;
import 'package:timezone/timezone.dart' as tz;
import 'package:http/http.dart' as http;
import 'package:android_intent_plus/android_intent.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:record/record.dart';
import 'package:flutter_contacts/flutter_contacts.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:overlay_support/overlay_support.dart';

// Bantu App Colors
const Color bantuPrimary = Color(0xFFE67E22);
const Color bantuSecondary = Color(0xFF2ECC71);
const Color bantuDark = Color(0xFF34495E);
const Color bantuLight = Color(0xFFECF0F1);
const Color bantuAccent = Color(0xFFF39C12);

// Bantu App Theme
final ThemeData bantuTheme = ThemeData(
  primaryColor: bantuPrimary,
  colorScheme: ColorScheme.fromSwatch().copyWith(
    primary: bantuPrimary,
    secondary: bantuSecondary,
  ),
  scaffoldBackgroundColor: bantuLight,
  appBarTheme: AppBarTheme(
    backgroundColor: bantuPrimary,
    foregroundColor: Colors.white,
    elevation: 0,
  ),
  floatingActionButtonTheme: FloatingActionButtonThemeData(
    backgroundColor: bantuPrimary,
    foregroundColor: Colors.white,
  ),
  inputDecorationTheme: InputDecorationTheme(
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: BorderSide.none,
    ),
    filled: true,
    fillColor: Colors.white.withOpacity(0.8),
  ),
);

final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Initialize timezone
  tz.initializeTimeZones();
  tz.setLocalLocation(tz.getLocation('Africa/Lagos'));

  // Initialize notifications
  final notifications = FlutterLocalNotificationsPlugin();
  const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
  const iosInit = DarwinInitializationSettings();
  const initSettings = InitializationSettings(android: androidInit, iOS: iosInit);

  await notifications.initialize(
    initSettings,
    onDidReceiveNotificationResponse: (response) {
      print('[Main] NotificationResponse received, payload: ${response.payload}');
      if (response.payload == 'alarm') {
        print('[Main] Navigating to AlarmRinging screen');
        navigatorKey.currentState?.pushNamed('/alarmRinging');
      }
    },
  );

  print('[Main] Initialization complete. Starting app...');
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});
  
  @override
  Widget build(BuildContext context) {
    return OverlaySupport(
      child: MaterialApp(
        navigatorKey: navigatorKey,
        debugShowCheckedModeBanner: false,
        theme: bantuTheme,
        home: const VoiceAgentScreen(),
        routes: {
          '/alarmRinging': (context) => const AlarmRingingScreen(),
        },
      ),
    );
  }
}

class AlarmRingingScreen extends StatefulWidget {
  const AlarmRingingScreen({super.key});
  @override
  State<AlarmRingingScreen> createState() => _AlarmRingingScreenState();
}

class _AlarmRingingScreenState extends State<AlarmRingingScreen> {
  final AudioPlayer player = AudioPlayer();

  @override
  void initState() {
    super.initState();
    print('[AlarmRingingScreen] initState: Alarm screen opened');

    player.setReleaseMode(ReleaseMode.loop);
    player.play(AssetSource('alarm.mp3')).then((_) {
      print('[AlarmRingingScreen] started playing alarm sound');
    }).catchError((e) {
      print('[AlarmRingingScreen] ERROR starting alarm sound: $e');
    });
  }

  @override
  void dispose() {
    print('[AlarmRingingScreen] dispose: stopping sound');
    player.stop();
    super.dispose();
  }

  void _dismissAlarm() {
    print('[AlarmRingingScreen] Dismiss pressed');
    player.stop();
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: bantuDark,
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.alarm, color: Colors.white, size: 120),
            const SizedBox(height: 20),
            const Text(
              "⏰ Bantu Alarm!",
              style: TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 40),
            ElevatedButton(
              onPressed: _dismissAlarm,
              style: ElevatedButton.styleFrom(
                backgroundColor: bantuPrimary,
                padding: const EdgeInsets.symmetric(horizontal: 30, vertical: 15),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              child: const Text("Dismiss", style: TextStyle(fontSize: 18)),
            )
          ],
        ),
      ),
    );
  }
}

class VoiceAgentScreen extends StatefulWidget {
  const VoiceAgentScreen({super.key});
  @override
  State<VoiceAgentScreen> createState() => _VoiceAgentScreenState();
}

class _VoiceAgentScreenState extends State<VoiceAgentScreen> {
  final AudioRecorder _audioRecorder = AudioRecorder();
  final AudioPlayer _audioPlayer = AudioPlayer();
  final FlutterLocalNotificationsPlugin _notifications = FlutterLocalNotificationsPlugin();

  String _transcript = '';
  String _reply = '';
  bool _isRecording = false;
  bool _isProcessing = false;
  String _selectedLanguage = 'en'; // Default to English
  bool _showOverlay = false;

  Timer? _silenceTimer;
  static const Duration silenceTimeout = Duration(seconds: 5);
  static const Duration maxRecordingTime = Duration(minutes: 3);

  @override
  void initState() {
    super.initState();
    print('[VoiceAgentScreen] initState');
    _initAudioPlayer();
    _initAudioRecorder();
    _requestContactsPermission();
  }

  @override
  void dispose() {
    print('[VoiceAgentScreen] dispose');
    _audioRecorder.dispose();
    _audioPlayer.dispose();
    _silenceTimer?.cancel();
    super.dispose();
  }

  Future<void> _requestContactsPermission() async {
    try {
      final status = await Permission.contacts.request();
      if (status.isGranted) {
        print('[VoiceAgentScreen] Contacts permission granted');
      } else {
        print('[VoiceAgentScreen] Contacts permission denied');
      }
    } catch (e) {
      print('[VoiceAgentScreen] ERROR requesting contacts permission: $e');
    }
  }

  Future<List<Map<String, String>>> _getContacts() async {
    try {
      // Check if we have contacts permission
      if (!await Permission.contacts.isGranted) {
        print('[VoiceAgentScreen] No contacts permission');
        return [];
      }

      // Get all contacts
      final contacts = await FlutterContacts.getContacts(
        withProperties: true,
        withPhoto: false,
      );

      // Convert to simple format for backend
      final List<Map<String, String>> contactList = [];
      
      for (final contact in contacts) {
        if (contact.name.first.isNotEmpty && contact.phones.isNotEmpty) {
          contactList.add({
            'name': contact.displayName,
            'phone': contact.phones.first.number
          });
        }
      }

      print('[VoiceAgentScreen] Found ${contactList.length} contacts');
      return contactList;
    } catch (e) {
      print('[VoiceAgentScreen] ERROR getting contacts: $e');
      return [];
    }
  }

  Future<void> _initAudioPlayer() async {
    try {
      // Set up audio player for better handling of TTS responses
      _audioPlayer.setReleaseMode(ReleaseMode.release);
      _audioPlayer.setVolume(1.0);
      
      // Set the audio context for better compatibility
      await _audioPlayer.setPlayerMode(PlayerMode.mediaPlayer);
      
      print('[VoiceAgentScreen] Audio player initialized');
    } catch (e) {
      print('[VoiceAgentScreen] ERROR initializing audio player: $e');
    }
  }

  Future<void> _initAudioRecorder() async {
    try {
      if (await _audioRecorder.hasPermission()) {
        print('[VoiceAgentScreen] Audio recording permission granted');
      } else {
        print('[VoiceAgentScreen] Audio recording permission denied');
        setState(() {
          _reply = 'Please grant microphone permission';
        });
      }
    } catch (e) {
      print('[VoiceAgentScreen] ERROR checking audio permission: $e');
      setState(() {
        _reply = 'Error checking microphone permission: $e';
      });
    }
  }

  Future<void> _startRecording() async {
    print('[VoiceAgentScreen] _startRecording called');
    try {
      // Check permission again
      if (!await _audioRecorder.hasPermission()) {
        setState(() {
          _reply = 'Microphone permission not granted';
        });
        return;
      }

      // Start recording to a temporary file with WAV format (better compatibility)
      final tempDir = await getTemporaryDirectory();
      final filePath = '${tempDir.path}/recording_${DateTime.now().millisecondsSinceEpoch}.wav';
      
      // Use WAV format for better compatibility with Spitch
      final config = RecordConfig(
        encoder: AudioEncoder.wav,
        bitRate: 128000,
        sampleRate: 16000, // Standard sample rate for speech
        numChannels: 1, // Mono audio
      );
      
      await _audioRecorder.start(config, path: filePath);
      
      setState(() {
        _transcript = '';
        _reply = '';
        _isRecording = true;
        _showOverlay = true;
      });
      print('[VoiceAgentScreen] Recording started: $filePath');

      // Set a timer to stop recording after silence timeout
      _silenceTimer?.cancel();
      _silenceTimer = Timer(silenceTimeout, () async {
        print('[VoiceAgentScreen] Silence timer fired');
        await _stopRecordingAndSend();
      });

      // Set a maximum recording time of 3 minutes
      Timer(maxRecordingTime, () async {
        if (_isRecording) {
          print('[VoiceAgentScreen] Maximum recording time reached');
          await _stopRecordingAndSend();
        }
      });
    } catch (e) {
      print('[VoiceAgentScreen] ERROR starting recording: $e');
      setState(() {
        _reply = 'Recording failed: $e';
        _isRecording = false;
        _showOverlay = false;
      });
    }
  }

  Future<void> _stopRecordingAndSend() async {
    if (!_isRecording) {
      print('[VoiceAgentScreen] _stopRecordingAndSend called, but not recording');
      return;
    }
    
    print('[VoiceAgentScreen] Stopping recording');
    try {
      final recordingPath = await _audioRecorder.stop();
      setState(() {
        _isRecording = false;
        _showOverlay = false;
      });

      if (recordingPath != null) {
        await _sendAudioToBackend(recordingPath);
      } else {
        print('[VoiceAgentScreen] No recording path returned');
        setState(() {
          _reply = 'Recording failed: no audio captured';
        });
      }
    } catch (e) {
      print('[VoiceAgentScreen] ERROR stopping recording: $e');
      setState(() {
        _reply = 'Error stopping recording: $e';
        _showOverlay = false;
      });
    }
  }

  Future<void> _sendAudioToBackend(String audioPath) async {
    print('[VoiceAgentScreen] _sendAudioToBackend with audio: "$audioPath"');
    setState(() {
      _isProcessing = true;
      _reply = '🤖 Processing...';
    });

    try {
      // Read the audio file
      final audioFile = File(audioPath);
      final audioBytes = await audioFile.readAsBytes();
      
      // Check file size
      if (audioBytes.length > 25 * 1024 * 1024) {
        setState(() {
          _reply = 'Audio file too large (max 25MB)';
          _isProcessing = false;
        });
        return;
      }

      // Get device contacts
      final contacts = await _getContacts();
      
      // Create multipart request
      var request = http.MultipartRequest(
        'POST', 
        Uri.parse('http://192.168.0.144:8000/agent_audio')
      );
      
      // Add audio file
      request.files.add(http.MultipartFile.fromBytes(
        'audio_file', 
        audioBytes,
        filename: 'recording.wav',
        contentType: MediaType('audio', 'wav'),
      ));
      
      // Add language parameter
      request.fields['language'] = _selectedLanguage;
      
      // Add contacts if available
      if (contacts.isNotEmpty) {
        request.fields['contacts'] = jsonEncode(contacts);
      }
      
      // Send request
      final response = await request.send();
      final responseBody = await response.stream.bytesToString();
      print('[VoiceAgentScreen] Backend responded: $responseBody');
      
      final data = jsonDecode(responseBody);
      
      // Check for errors
      if (data.containsKey('error')) {
        throw Exception(data['error']);
      }

      // Extract data from backend response
      final action = data['action'];
      final backendReply = data['reply'] ?? '';
      final ttsAudio = data['tts_audio'];
      
      // Handle alarm time extraction more reliably
      String? timeStr;
      if (data['phone_number'] is Map && data['phone_number']['time'] != null) {
        timeStr = data['phone_number']['time'];
      } else if (data['analysis'] is Map && data['analysis']['parameters'] is Map) {
        timeStr = data['analysis']['parameters']['datetime'] ?? 
                 data['analysis']['parameters']['alarm_time'];
      }
      
      // Extract phone number for calling
      String? phoneNumber;
      if (data['phone_number'] is String) {
        phoneNumber = data['phone_number'];
      } else if (data['phone_number'] is Map && data['phone_number']['number'] != null) {
        phoneNumber = data['phone_number']['number'].toString();
      }

      // Update transcript if available
      if (data['text'] != null) {
        setState(() {
          _transcript = data['text'];
        });
      }

      setState(() {
        _reply = backendReply;
      });
      print('[VoiceAgentScreen] Backend action: $action, time: $timeStr, phone: $phoneNumber');

      // Handle different actions from backend
      if (action == 'set_alarm' && timeStr != null) {
        bool ok = await _scheduleAlarm(timeStr);
        if (!ok) {
          setState(() {
            _reply = '⚠️ Failed to set alarm';
          });
        }
      } 
      // Handle call action
      else if (action == 'make_call' && phoneNumber != null) {
        _makePhoneCall(phoneNumber);
      }
      // Handle SMS action
      else if (action == 'send_sms' && phoneNumber != null) {
        // Extract message from parameters if available
        final message = data['analysis'] != null && data['analysis']['parameters'] != null 
            ? data['analysis']['parameters']['message'] ?? "Hello from Bantu Voice Assistant"
            : "Hello from Bantu Voice Assistant";
        _sendSms(phoneNumber, message);
      }

      // Play TTS audio if available
      if (ttsAudio != null && ttsAudio.isNotEmpty) {
        await _playTtsAudio(ttsAudio);
      } else {
        print('[VoiceAgentScreen] No TTS audio received from backend');
      }
    } catch (e) {
      print('[VoiceAgentScreen] ERROR in sendAudioToBackend: $e');
      setState(() {
        _reply = '⚠️ Error contacting backend: $e';
      });
    } finally {
      setState(() {
        _isProcessing = false;
      });
      
      // Clean up the audio file
      try {
        final audioFile = File(audioPath);
        if (await audioFile.exists()) {
          await audioFile.delete();
        }
      } catch (e) {
        print('[VoiceAgentScreen] Error deleting temp audio file: $e');
      }
    }
  }

  Future<void> _playTtsAudio(String ttsAudio) async {
    try {
      print('[VoiceAgentScreen] Playing TTS audio, length: ${ttsAudio.length}');
      
      // Decode base64 audio
      final bytes = base64Decode(ttsAudio);
      
      // Try different approaches to play the audio
      bool playbackSuccess = false;
      
      // Approach 1: Try playing directly from bytes
      try {
        await _audioPlayer.play(BytesSource(bytes));
        print('[VoiceAgentScreen] Playing TTS audio from bytes');
        playbackSuccess = true;
      } catch (e) {
        print('[VoiceAgentScreen] BytesSource failed: $e');
      }
      
      // Approach 2: Try saving to file and playing
      if (!playbackSuccess) {
        try {
          final tempDir = await getTemporaryDirectory();
          final tempFile = File('${tempDir.path}/tts_response_${DateTime.now().millisecondsSinceEpoch}.wav');
          await tempFile.writeAsBytes(bytes);
          
          await _audioPlayer.play(DeviceFileSource(tempFile.path));
          print('[VoiceAgentScreen] Playing TTS audio from file: ${tempFile.path}');
          playbackSuccess = true;
          
          // Clean up the file after playback completes
          _audioPlayer.onPlayerComplete.listen((event) {
            print('[VoiceAgentScreen] TTS playback completed');
            tempFile.delete().then((_) {
              print('[VoiceAgentScreen] Temporary audio file deleted');
            });
          });
        } catch (e) {
          print('[VoiceAgentScreen] DeviceFileSource failed: $e');
        }
      }
      
      // Approach 3: Try using low latency mode
      if (!playbackSuccess) {
        try {
          await _audioPlayer.setPlayerMode(PlayerMode.lowLatency);
          final tempDir = await getTemporaryDirectory();
          final tempFile = File('${tempDir.path}/tts_response_${DateTime.now().millisecondsSinceEpoch}.wav');
          await tempFile.writeAsBytes(bytes);
          
          await _audioPlayer.play(DeviceFileSource(tempFile.path));
          print('[VoiceAgentScreen] Playing TTS audio with low latency mode');
          playbackSuccess = true;
          
          // Clean up the file after playback completes
          _audioPlayer.onPlayerComplete.listen((event) {
            print('[VoiceAgentScreen] TTS playback completed');
            tempFile.delete().then((_) {
              print('[VoiceAgentScreen] Temporary audio file deleted');
            });
          });
        } catch (e) {
          print('[VoiceAgentScreen] Low latency mode failed: $e');
        }
      }
      
      if (!playbackSuccess) {
        print('[VoiceAgentScreen] All audio playback methods failed');
        setState(() {
          _reply = 'Audio response failed: ${_reply}';
        });
      }
    } catch (e) {
      print('[VoiceAgentScreen] ERROR playing TTS audio: $e');
      // Fallback to text response if audio fails
      setState(() {
        _reply = 'Audio response failed: ${_reply}';
      });
    }
  }

  // Function to make phone calls
  void _makePhoneCall(String phoneNumber) async {
    final url = 'tel:$phoneNumber';
    if (await canLaunch(url)) {
      await launch(url);
      setState(() {
        _reply = 'Calling $phoneNumber...';
      });
    } else {
      setState(() {
        _reply = 'Could not launch phone app';
      });
    }
  }

  // Function to send SMS
  void _sendSms(String phoneNumber, String message) async {
    final url = 'sms:$phoneNumber?body=${Uri.encodeComponent(message)}';
    if (await canLaunch(url)) {
      await launch(url);
      setState(() {
        _reply = 'Sending message to $phoneNumber...';
      });
    } else {
      setState(() {
        _reply = 'Could not launch messaging app';
      });
    }
  }

  Future<bool> _scheduleAlarm(String time) async {
    try {
      print('[scheduleAlarm] Raw time: $time');
      
      // Handle different time formats from backend
      DateTime alarmTime;
      if (time.contains('T')) {
        // ISO format
        alarmTime = DateTime.parse(time);
      } else {
        // Try parsing with dateparser format
        final parsedTime = DateTime.tryParse(time);
        if (parsedTime != null) {
          alarmTime = parsedTime;
        } else {
          // Fallback: assume it's a time string like "10:30"
          final now = DateTime.now();
          final timeParts = time.split(':');
          if (timeParts.length >= 2) {
            final hour = int.tryParse(timeParts[0]) ?? now.hour;
            final minute = int.tryParse(timeParts[1]) ?? now.minute;
            alarmTime = DateTime(now.year, now.month, now.day, hour, minute);
            // If the time has already passed today, schedule for tomorrow
            if (alarmTime.isBefore(now)) {
              alarmTime = alarmTime.add(const Duration(days: 1));
            }
          } else {
            throw Exception('Unrecognized time format: $time');
          }
        }
      }
      
      print('[scheduleAlarm] Parsed alarmTime: $alarmTime');

      const androidDetails = AndroidNotificationDetails(
        'alarm_channel',
        'Alarms',
        channelDescription: 'Channel for full-screen alarms',
        importance: Importance.max,
        priority: Priority.high,
        fullScreenIntent: true,
        playSound: true,
        sound: null,
      );
      const iosDetails = DarwinNotificationDetails(presentSound: true);
      final notificationDetails = NotificationDetails(android: androidDetails, iOS: iosDetails);

      await _notifications.zonedSchedule(
        0,
        '⏰ Bantu Alarm',
        'It\'s time!',
        tz.TZDateTime.from(alarmTime, tz.local),
        notificationDetails,
        androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
        matchDateTimeComponents: DateTimeComponents.time,
        payload: 'alarm',
      );

      print('[scheduleAlarm] Alarm scheduled for ${alarmTime.toLocal()}');

      // Android native alarm intent (optional)
      if (Platform.isAndroid) {
        final intent = AndroidIntent(
          action: 'android.intent.action.SET_ALARM',
          arguments: <String, dynamic>{
            'android.intent.extra.alarm.HOUR': alarmTime.hour,
            'android.intent.extra.alarm.MINUTES': alarmTime.minute,
            'android.intent.extra.alarm.MESSAGE': 'Bantu Alarm',
            'android.intent.extra.alarm.SKIP_UI': true,  // skip UI if possible
          },
        );
        await intent.launch();
        print('[scheduleAlarm] Android native alarm intent launched');
      }

      setState(() {
        final formatted = "${alarmTime.hour.toString().padLeft(2, '0')}:${alarmTime.minute.toString().padLeft(2, '0')}";
        _reply = "✅ Alarm set for $formatted";
      });

      return true;
    } catch (e) {
      print('[scheduleAlarm] Error: $e');
      setState(() {
        _reply = '❌ Error setting alarm: $e';
      });
      return false;
    }
  }

  Widget _buildLanguageSelector() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(20),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.1),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: DropdownButton<String>(
        value: _selectedLanguage,
        underline: const SizedBox(),
        icon: const Icon(Icons.arrow_drop_down, color: bantuPrimary),
        items: const [
          DropdownMenuItem(value: 'en', child: Text('English')),
          DropdownMenuItem(value: 'yo', child: Text('Yoruba')),
          DropdownMenuItem(value: 'ha', child: Text('Hausa')),
          DropdownMenuItem(value: 'ig', child: Text('Igbo')),
        ],
        onChanged: (String? newValue) {
          if (newValue != null) {
            setState(() {
              _selectedLanguage = newValue;
            });
          }
        },
      ),
    );
  }

  Widget _buildMicButton() {
    return FloatingActionButton(
      onPressed: (_isRecording || _isProcessing) ? null : _startRecording,
      backgroundColor: bantuPrimary,
      child: Icon(
        _isRecording ? Icons.stop : Icons.mic,
        size: 30,
      ),
    );
  }

  Widget _buildOverlay() {
    if (!_showOverlay) return const SizedBox.shrink();

    return Positioned(
      bottom: 100,
      left: 0,
      right: 0,
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 20),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: bantuDark.withOpacity(0.9),
          borderRadius: BorderRadius.circular(16),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.2),
              blurRadius: 8,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Column(
          children: [
            const Text(
              "Bantu is listening...",
              style: TextStyle(color: Colors.white, fontSize: 16),
            ),
            const SizedBox(height: 10),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.mic, color: bantuSecondary, size: 20),
                const SizedBox(width: 8),
                Text(
                  "Speak now",
                  style: TextStyle(color: bantuLight, fontSize: 14),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ElevatedButton(
              onPressed: _stopRecordingAndSend,
              style: ElevatedButton.styleFrom(
                backgroundColor: bantuPrimary,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(20),
                ),
              ),
              child: const Text("Stop", style: TextStyle(color: Colors.white)),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Bantu Voice Assistant'),
        centerTitle: true,
        actions: [
          IconButton(
            icon: const Icon(Icons.info_outline),
            onPressed: () {
              showDialog(
                context: context,
                builder: (context) => AlertDialog(
                  title: const Text("About Bantu"),
                  content: const Text(
                    "Bantu is a multilingual voice assistant that supports English, Yoruba, Hausa, and Igbo. "
                    "It can help you with tasks, set reminders, make calls, and more.",
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text("OK"),
                    ),
                  ],
                ),
              );
            },
          ),
        ],
      ),
      body: Stack(
        children: [
          Padding(
            padding: const EdgeInsets.all(20.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SizedBox(height: 20),
                Text(
                  "Welcome to Bantu",
                  style: TextStyle(
                    fontSize: 28,
                    fontWeight: FontWeight.bold,
                    color: bantuDark,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  "Your multilingual voice assistant",
                  style: TextStyle(
                    fontSize: 16,
                    color: bantuDark.withOpacity(0.7),
                  ),
                ),
                const SizedBox(height: 30),
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(16),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withOpacity(0.1),
                        blurRadius: 8,
                        offset: const Offset(0, 4),
                      ),
                    ],
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        "I can help you with:",
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                          color: bantuDark,
                        ),
                      ),
                      const SizedBox(height: 10),
                      _buildFeatureItem("📞 Calls and messages"),
                      _buildFeatureItem("⏰ Alarms and reminders"),
                      _buildFeatureItem("🗓️ Calendar scheduling"),
                      _buildFeatureItem("❓ Ask Generel Questions"),
                      _buildFeatureItem("🧮 Calculations"),
                    ],
                  ),
                ),
                const SizedBox(height: 30),
                if (_transcript.isNotEmpty) ...[
                  Text(
                    "You said:",
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: bantuDark,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: bantuLight,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      _transcript,
                      style: const TextStyle(fontSize: 16),
                    ),
                  ),
                  const SizedBox(height: 20),
                ],
                if (_reply.isNotEmpty) ...[
                  Text(
                    "Bantu:",
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: bantuDark,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: bantuSecondary.withOpacity(0.2),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      _reply,
                      style: TextStyle(
                        fontSize: 16,
                        color: bantuDark,
                      ),
                    ),
                  ),
                ],
                const Spacer(),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      "Language:",
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w500,
                        color: bantuDark,
                      ),
                    ),
                    _buildLanguageSelector(),
                  ],
                ),
                const SizedBox(height: 20),
                Center(
                  child: _buildMicButton(),
                ),
                const SizedBox(height: 20),
              ],
            ),
          ),
          _buildOverlay(),
        ],
      ),
    );
  }

  Widget _buildFeatureItem(String text) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(Icons.check_circle, color: bantuSecondary, size: 20),
          const SizedBox(width: 10),
          Text(text, style: TextStyle(fontSize: 16, color: bantuDark)),
        ],
      ),
    );
  }
}