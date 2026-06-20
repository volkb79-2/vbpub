#!/usr/bin/env python3
"""
System Information and Telegram Notification Orchestrator
Uses modular components: telegram_client, system_info, geekbench_runner
"""

import argparse
import os
import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    from telegram_client import TelegramClient
    from system_info import SystemInfo
    from geekbench_runner import GeekbenchRunner
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Ensure telegram_client.py, system_info.py, and geekbench_runner.py are in the same directory")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='System Information and Telegram Notifications',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --collect                    # Collect and display system info
  %(prog)s --notify                     # Send system info via Telegram
  %(prog)s --test-mode                  # Test Telegram connection
  %(prog)s --notify --geekbench         # Run Geekbench and send results
  %(prog)s --geekbench-only             # Only run Geekbench (no Telegram)

Environment Variables:
  TELEGRAM_BOT_TOKEN    Bot token from @BotFather
  TELEGRAM_CHAT_ID      Your chat ID or channel ID

Getting Chat ID:
  1. Send message to @userinfobot
  2. Or use: curl https://api.telegram.org/botYOUR_TOKEN/getUpdates
  3. For channels: Use @yourchannel or numeric ID (-1001234567890)

IMPORTANT: Send a message to your bot first before it can message you!
        """
    )
    
    parser.add_argument('--collect', action='store_true',
                       help='Collect and display system information')
    parser.add_argument('--notify', action='store_true',
                       help='Send notification to Telegram')
    parser.add_argument('--test-mode', action='store_true',
                       help='Test Telegram connection only')
    parser.add_argument('--geekbench', action='store_true',
                       help='Run Geekbench benchmark (requires --notify or --geekbench-only)')
    parser.add_argument('--geekbench-only', action='store_true',
                       help='Only run Geekbench without Telegram')
    parser.add_argument('--geekbench-version', type=int, default=6, choices=[6],
                       help='Geekbench version (Geekbench 6 only)')
    parser.add_argument('--bot-token', metavar='TOKEN',
                       default=os.environ.get('TELEGRAM_BOT_TOKEN'),
                       help='Telegram bot token')
    parser.add_argument('--chat-id', metavar='ID',
                       default=os.environ.get('TELEGRAM_CHAT_ID'),
                       help='Telegram chat ID or channel username')
    parser.add_argument('--output', '-o', metavar='FILE',
                       help='Save system info to JSON file')
    parser.add_argument('--caption', metavar='TEXT',
                       help='Custom caption/header for system info message')
    
    args = parser.parse_args()
    
    # Initialize Telegram client if needed
    telegram = None
    if args.notify or args.test_mode:
        if not args.bot_token or not args.chat_id:
            print("Error: Bot token and chat ID required for Telegram operations")
            print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables")
            print("Or use --bot-token and --chat-id arguments")
            return 1
        
        try:
            telegram = TelegramClient(args.bot_token, args.chat_id)
        except ValueError as e:
            print(f"Error: {e}")
            return 1
    
    # Test mode
    if args.test_mode:
        print("Testing Telegram connection...")
        if telegram.test_connection():
            print("\nSending test message...")
            from datetime import datetime
            test_msg = f"<b>✓ Test Message</b>\n\nBot is working correctly!\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            if telegram.send_message(test_msg):
                print("✓ Test message sent successfully!")
                return 0
            else:
                print("✗ Failed to send test message")
                return 1
        else:
            print("✗ Bot connection test failed")
            return 1
    
    # Collect system info
    if args.collect or args.notify or args.output:
        print("Collecting system information...")
        collector = SystemInfo()
        info = collector.collect()
        
        if args.collect:
            import json
            print(json.dumps(info, indent=2))
        
        if args.output:
            import json
            with open(args.output, 'w') as f:
                json.dump(info, f, indent=2)
            print(f"System info saved to {args.output}")
        
        if args.notify:
            print("Sending system info to Telegram...")
            if args.caption and "BEFORE setup" in args.caption:
                message = collector.format_html_before_setup()
            else:
                message = collector.format_html()
            
            # Add custom caption if provided
            if args.caption:
                message = f"<b>{args.caption}</b>\n\n{message}"
            
            if telegram.send_message(message):
                print("✓ System info sent successfully!")
            else:
                print("✗ Failed to send system info")
                return 1
    
    # Run Geekbench
    if args.geekbench or args.geekbench_only:
        print("\n" + "="*60)
        print("Running Geekbench benchmark...")
        print("="*60 + "\n")
        
        runner = GeekbenchRunner(version=args.geekbench_version)
        
        try:
            # Download and extract
            if not runner.download_and_extract():
                error_msg = "❌ <b>Geekbench Download Failed</b>\n\nFailed to download or extract Geekbench."
                print("✗ Failed to download/extract Geekbench")
                
                # Send failure notification via Telegram
                if args.notify and telegram:
                    telegram.send_message(error_msg)
                
                return 1
            
            # Run benchmark
            if not runner.run_benchmark():
                # Extract error details from runner.results
                error_details = runner.results.get('error', 'Unknown error')
                error_msg = f"❌ <b>Geekbench Benchmark Failed</b>\n\n<b>Error:</b> {error_details}"
                
                # Add stdout/stderr if available
                if runner.results.get('stdout'):
                    stdout_excerpt = runner.results['stdout'][:500]  # Limit to 500 chars
                    error_msg += f"\n\n<b>Output excerpt:</b>\n<code>{stdout_excerpt}</code>"
                
                if runner.results.get('stderr'):
                    stderr_excerpt = runner.results['stderr'][:500]
                    error_msg += f"\n\n<b>Error output:</b>\n<code>{stderr_excerpt}</code>"
                
                print("✗ Benchmark failed")
                
                # Send failure notification via Telegram
                if args.notify and telegram:
                    telegram.send_message(error_msg)
                
                return 1
            
            # Get summary
            summary = runner.get_summary()
            print("\n" + summary)
            
            # For free version, URLs are already in results from run_benchmark
            # For pro version, need to check if manual upload is needed
            urls = None
            if not runner.results.get('free_version'):
                # Pro version - try to upload if not already uploaded
                urls = runner.upload_results()
            else:
                # Free version already uploaded, extract URLs from results
                if 'result_url' in runner.results:
                    urls = {
                        'result_url': runner.results['result_url']
                    }
                    if 'claim_url' in runner.results:
                        urls['claim_url'] = runner.results['claim_url']
            
            # Send via Telegram if enabled
            if args.notify and telegram:
                full_message = summary
                # URLs are already in summary for free version, but add them again for backwards compatibility
                if urls and not runner.results.get('free_version'):
                    full_message += f"\n<b>🔗 Results:</b>\n"
                    full_message += f"  {urls.get('result_url', 'N/A')}\n"
                    if 'claim_url' in urls:
                        full_message += f"\n<b>📌 Claim URL:</b>\n  {urls['claim_url']}\n"
                
                if telegram.send_message(full_message):
                    print("\n✓ Geekbench results sent to Telegram!")
                else:
                    print("\n✗ Failed to send Geekbench results")
            
            return 0
            
        finally:
            runner.cleanup()
    
    if not (args.collect or args.notify or args.test_mode or args.geekbench or args.geekbench_only):
        parser.print_help()
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
