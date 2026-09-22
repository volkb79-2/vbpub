#!/usr/bin/env python3
"""
Convert audio files to WAV format suitable for speech processing
"""
import os
import sys
from pydub import AudioSegment

def convert_m4a_to_wav(input_path, output_path):
    """Convert M4A to WAV with proper format for speech processing"""
    try:
        # Load the M4A file
        audio = AudioSegment.from_file(input_path, format="m4a")
        
        # Convert to mono, 16kHz, 16-bit (standard for speech)
        audio = audio.set_channels(1)  # Mono
        audio = audio.set_frame_rate(16000)  # 16kHz sample rate
        audio = audio.set_sample_width(2)  # 16-bit
        
        # Export as WAV
        audio.export(output_path, format="wav")
        
        print(f"✅ Converted: {input_path} -> {output_path}")
        print(f"   Duration: {len(audio) / 1000:.2f} seconds")
        print(f"   Channels: {audio.channels}")
        print(f"   Sample Rate: {audio.frame_rate} Hz")
        print(f"   Sample Width: {audio.sample_width * 8} bit")
        
        return True
        
    except Exception as e:
        print(f"❌ Error converting {input_path}: {e}")
        return False

if __name__ == "__main__":
    input_file = "testdata/Aufnahme.m4a" 
    output_file = "testdata/Aufnahme.wav"
    
    if not os.path.exists(input_file):
        print(f"❌ Input file not found: {input_file}")
        sys.exit(1)
        
    success = convert_m4a_to_wav(input_file, output_file)
    sys.exit(0 if success else 1)