use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::{Arc, Mutex};
use std::io::BufWriter;

pub struct AudioRecorder {
    samples: Arc<Mutex<Vec<f32>>>,
    is_recording: Arc<Mutex<bool>>,
    sample_rate: u32,
}

impl AudioRecorder {
    pub fn new() -> Self {
        Self {
            samples: Arc::new(Mutex::new(Vec::new())),
            is_recording: Arc::new(Mutex::new(false)),
            sample_rate: 16000, // Default sample rate for speech recognition
        }
    }

    /// Start recording audio from the default input device
    pub fn start_recording(&mut self) -> Result<(), String> {
        let host = cpal::default_host();
        let device = host
            .default_input_device()
            .ok_or_else(|| "No input device available".to_string())?;

        let config = device
            .default_input_config()
            .map_err(|e| format!("Failed to get default input config: {}", e))?;

        // Clear previous samples
        self.samples.lock().unwrap().clear();
        
        // Set recording flag
        *self.is_recording.lock().unwrap() = true;

        let samples = Arc::clone(&self.samples);
        let is_recording = Arc::clone(&self.is_recording);
        
        // Store the actual sample rate from the device
        self.sample_rate = config.sample_rate().0;

        let stream = match config.sample_format() {
            cpal::SampleFormat::F32 => self.build_stream::<f32>(&device, &config.into(), samples, is_recording),
            cpal::SampleFormat::I16 => self.build_stream::<i16>(&device, &config.into(), samples, is_recording),
            cpal::SampleFormat::U16 => self.build_stream::<u16>(&device, &config.into(), samples, is_recording),
            _ => return Err("Unsupported sample format".to_string()),
        }?;

        stream.play().map_err(|e| format!("Failed to play stream: {}", e))?;
        
        // Keep the stream alive by leaking it (it will be cleaned up when app exits)
        // In a production app, you'd want to store this properly and clean it up
        std::mem::forget(stream);

        Ok(())
    }

    fn build_stream<T>(
        &self,
        device: &cpal::Device,
        config: &cpal::StreamConfig,
        samples: Arc<Mutex<Vec<f32>>>,
        is_recording: Arc<Mutex<bool>>,
    ) -> Result<cpal::Stream, String>
    where
        T: cpal::Sample,
    {
        let err_fn = |err| eprintln!("Error in audio stream: {}", err);

        let stream = device
            .build_input_stream(
                config,
                move |data: &[T], _: &cpal::InputCallbackInfo| {
                    if *is_recording.lock().unwrap() {
                        let mut samples = samples.lock().unwrap();
                        for &sample in data.iter() {
                            samples.push(sample.to_f32());
                        }
                    }
                },
                err_fn,
                None,
            )
            .map_err(|e| format!("Failed to build input stream: {}", e))?;

        Ok(stream)
    }

    /// Stop recording and return the audio data as base64-encoded WAV
    pub fn stop_recording(&mut self) -> Result<String, String> {
        *self.is_recording.lock().unwrap() = false;
        
        let samples = self.samples.lock().unwrap();
        if samples.is_empty() {
            return Err("No audio data recorded".to_string());
        }

        // Convert samples to WAV format
        let wav_data = self.samples_to_wav(&samples)?;
        
        // Encode as base64
        Ok(base64::encode(&wav_data))
    }

    /// Convert f32 samples to WAV format bytes
    fn samples_to_wav(&self, samples: &[f32]) -> Result<Vec<u8>, String> {
        let mut cursor = std::io::Cursor::new(Vec::new());
        
        {
            let spec = hound::WavSpec {
                channels: 1,
                sample_rate: self.sample_rate,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            };

            let mut writer = hound::WavWriter::new(&mut cursor, spec)
                .map_err(|e| format!("Failed to create WAV writer: {}", e))?;

            for &sample in samples.iter() {
                let sample_i16 = (sample * i16::MAX as f32) as i16;
                writer
                    .write_sample(sample_i16)
                    .map_err(|e| format!("Failed to write sample: {}", e))?;
            }

            writer
                .finalize()
                .map_err(|e| format!("Failed to finalize WAV: {}", e))?;
        }

        Ok(cursor.into_inner())
    }

    pub fn is_recording(&self) -> bool {
        *self.is_recording.lock().unwrap()
    }
}
