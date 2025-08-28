# Overview

This is a Telegram bot designed for subscription and client management with WhatsApp messaging capabilities. The bot allows users to manage clients, track subscription payments, send automated reminders, and handle billing cycles. It integrates with Mercado Pago for payment processing and Bayleys API for WhatsApp messaging, providing a complete solution for service providers to manage their customer base and automate communication.

# Recent Changes

## 2025-08-23: Dashboard Auto-Update After Client Renewal (Latest)
- **Auto-Dashboard Refresh**: Dashboard automatically updates and provides access button after manual client renewals
- **Renewal Feedback Enhancement**: All renewal processes now show "Dashboard atualizado com novas estatísticas!" message
- **Quick Dashboard Access**: Added "📊 Ver Dashboard" button in all client renewal success screens
- **Smart Navigation**: Enhanced renewal workflows with direct access to updated dashboard statistics
- **Three Renewal Types Covered**: Auto-renewal (30 days), custom date renewal, and edit due date all trigger dashboard updates
- **User Experience Improvement**: Immediate visibility of financial changes after client management actions

## 2025-08-23: Dashboard Monthly Statistics Enhancement (Earlier)
- **Monthly Financial Overview**: Added comprehensive monthly statistics to dashboard showing paid vs pending clients
- **Real-time Monthly Tracking**: Dashboard now displays "Pagos" (paid), "A Pagar" (to pay), and "Total do Mês" for current month
- **Smart Monthly Calculations**: System automatically calculates clients due in current month and those who have already paid
- **Enhanced Dashboard Interface**: Improved visual layout with 💰💵📈📋 icons for better financial overview
- **Current Month Display**: Shows month/year (MM/YYYY format) for clear monthly context
- **Dual Dashboard Support**: Updated both callback (inline buttons) and keyboard-based dashboard versions

## 2025-08-23: Automatic Payment Verification System (Earlier)
- **Automated Payment Processing**: Implemented automatic payment verification every 2 minutes using scheduler service
- **Real-time Payment Detection**: System automatically detects approved PIX payments without manual intervention
- **Instant Account Activation**: Accounts are automatically activated immediately when payment is approved
- **Auto-Notification System**: Users receive immediate notification via Telegram when payment is processed
- **PIX Code Enhancement**: Improved PIX code formatting with code blocks and clear copying instructions
- **Payment Cleanup**: Automatic cleanup of expired payments (over 24 hours) to maintain database hygiene
- **Comprehensive Logging**: Enhanced logging system for payment processing and error tracking
- **Zero Manual Intervention**: Complete automation from payment to account activation and user notification

## 2025-08-22: WhatsApp Concurrent Connections Fix (Earlier)
- **Connection Semaphore**: Implemented intelligent semaphore to limit concurrent connections and prevent conflicts
- **Browser Isolation**: Each user now has unique browser identification preventing session conflicts
- **Random Delays**: Added random delays between connection attempts to prevent simultaneous connection blocks
- **Improved Error Handling**: Enhanced error recovery with exponential backoff for failed connections
- **Connection Throttling**: Limited to max 2 concurrent connections to prevent WhatsApp API conflicts
- **Better QR Management**: Improved QR code generation with proper timeout and error handling
- **Session State Management**: Enhanced connection state management to prevent interference between users
- **Conflict Resolution**: Resolved stream errors (status 440) that prevented multiple users from connecting

## 2025-08-22: WhatsApp Session Persistence Enhancement (Earlier)
- **Session Recovery System**: Implemented intelligent session restore mechanisms that preserve WhatsApp connections during code updates
- **Auto-Backup**: Created automatic backup system for WhatsApp sessions before any connection resets
- **Health Monitoring**: Added health check endpoint (/health) and auto-recovery system that checks sessions every 5 minutes
- **Improved Timeouts**: Increased connection timeouts and improved retry logic for better stability
- **Session Restoration**: Added automatic session restore when WhatsApp becomes disconnected
- **Graceful Shutdown**: Implemented proper session cleanup on server restart to prevent corruption
- **Persistent Storage**: Enhanced session storage in ./sessions/ directory with backup mechanisms
- **Connection Intelligence**: Smart reconnection logic that preserves existing auth data when possible

## 2025-08-22: Evening Reminder System Removal (Earlier)
- **Functionality Removed**: Completely removed evening reminder system and all related features
- **Database Updates**: Removed evening_reminder_time and last_evening_run columns from user_schedule_settings table
- **Interface Simplification**: Removed "Alterar Horário Noturno" button and configuration options from schedule settings
- **Code Cleanup**: Eliminated set_evening_time_callback function, SCHEDULE_WAITING_EVENING_TIME state, and handle_schedule_evening_time handler
- **Scheduler Optimization**: Removed _process_evening_reminders_for_user function and evening processing logic
- **User Experience**: Simplified schedule configuration interface to only show morning reminders and daily reports
- **System Stability**: All existing functionality remains intact, system operates normally with morning-only reminder schedule

## 2025-08-22: Robust Automated Scheduling System (Final)
- **Problem Fixed**: Completely resolved automatic sending failures when system restarts or timing is missed
- **Smart Execution Logic**: Changed from exact time matching (==) to time-passed checking (>=) to catch missed executions
- **Execution Tracking**: Added last_morning_run, last_report_run columns to prevent duplicate daily executions
- **Recovery System**: System now executes pending automated tasks when it detects time has passed but execution hasn't happened today
- **Enhanced Error Handling**: Improved exception handling with timeout controls and detailed error logging
- **Database Enhancements**: Added tracking columns to ensure each automated task runs exactly once per day regardless of system restarts

## 2025-08-22: Individual Client Reminder Control System (Earlier)
- **Individual Client Control**: Added auto_reminders_enabled column to clients table for per-client reminder management
- **Client Details Interface Enhancement**: Added reminder status display and toggle buttons in client details screen
- **Toggle Functionality**: Users can now activate/deactivate automatic reminders for each client individually
- **Scheduler Filter Updates**: Modified all scheduler queries to only process clients with auto_reminders_enabled=True
- **Visual Status Indicators**: Client details now show ✅/❌ status for reminder preferences
- **Enhanced Logging**: Improved daily report logging for better debugging of notification issues
- **Database Migration**: Added auto_reminders_enabled column with default TRUE value for existing clients

## 2025-08-22: Schedule Management System Implementation (Earlier)
- **Custom Scheduling Interface**: Added "⏰ Horários" button in main menu for schedule configuration
- **User-Specific Schedule Settings**: Implemented UserScheduleSettings model for personalized timing
- **Configurable Times**: Users can set custom times for morning reminders and daily reports
- **Default Schedule Times**: Morning 09:00, Daily Report 08:00
- **Schedule Reset Functionality**: Option to reset all times to default settings with one click
- **Time Validation**: Input validation for HH:MM format with proper error handling
- **Scheduler Service Update**: Modified scheduler to check user-specific times every minute instead of fixed global times
- **Individual User Processing**: Separated reminder and notification processing per user based on their custom schedules

## 2025-08-22: Template System Implementation (Earlier)
- **Message Templates**: Created complete template system with 6 default templates
- **Template Variables**: Implemented dynamic variables ({nome}, {plano}, {valor}, {vencimento}, {servidor}, {informacoes_extras})
- **Automated Reminders**: System sends automatic reminders -2 days, -1 day, due date, +1 day overdue
- **Renewal Messages**: Added option to send renewal confirmation messages after client renewal
- **Template Management**: Full CRUD operations for templates with user interface via "📝 Templates" button

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Bot Framework
- **Telegram Bot API**: Built using python-telegram-bot library for handling user interactions
- **Conversation Handlers**: Implements state-based conversations for user registration and client management
- **Callback Query System**: Uses inline keyboards for menu navigation and action handling

## Database Layer
- **SQLAlchemy ORM**: Object-relational mapping for database operations with PostgreSQL
- **Connection Pooling**: Configured with pool pre-ping and recycling for reliable connections
- **Session Management**: Context manager pattern for automatic transaction handling and cleanup
- **Database Models**: User, Client, Subscription, MessageTemplate, MessageLog, and SystemSettings entities

## Payment Processing
- **Mercado Pago Integration**: PIX payment method for Brazilian market subscriptions
- **Webhook Handling**: Automated payment status updates and subscription management
- **Trial System**: 7-day trial period with automatic expiration handling
- **Subscription Lifecycle**: Monthly billing cycles with payment tracking and renewal management

## Messaging System
- **WhatsApp Integration**: Bayleys API for sending automated messages to clients
- **Template System**: Complete template management with 6 default types (welcome, reminder_2_days, reminder_1_day, reminder_due_date, reminder_overdue, renewal)
- **Template Variables**: Dynamic content replacement with client data ({nome}, {plano}, {valor}, {vencimento}, {servidor}, {informacoes_extras})
- **Scheduled Messaging**: Automated reminder system sends messages at 9 AM based on due dates
- **Renewal Notifications**: Optional renewal confirmation messages sent after client renewal
- **Message Logging**: Complete tracking of all sent messages with status and error handling

## Background Services
- **Scheduler Service**: Thread-based scheduling for automated tasks and reminders
- **Database Service**: Centralized database operations with connection management
- **Telegram Service**: Notification system for bot-to-user communication
- **Payment Service**: Payment creation and status verification handling

## Business Logic
- **Client Management**: CRUD operations for client data with subscription tracking
- **Reminder System**: Configurable reminder intervals (2 days before, 1 day before, due date, 1 day after)
- **User Registration**: Phone number validation and account setup process
- **Subscription Management**: Trial tracking, payment processing, and service activation/deactivation

## Error Handling and Logging
- **Comprehensive Logging**: File and console logging with configurable levels
- **Exception Management**: Try-catch blocks with proper error recovery and user feedback
- **Database Transactions**: Rollback mechanisms for failed operations
- **Service Reliability**: Retry logic and fallback mechanisms for external API calls

# External Dependencies

## Payment Gateway
- **Mercado Pago**: Brazilian payment processor for PIX and subscription payments
- **Webhook Integration**: Real-time payment status updates and subscription management

## Messaging Services
- **Telegram Bot API**: Primary user interface and notification system
- **Bayleys WhatsApp API**: Client communication and automated messaging platform

## Database
- **PostgreSQL**: Primary data storage for users, clients, subscriptions, and system data
- **Connection Management**: Pooled connections with automatic reconnection handling

## Infrastructure Services
- **Environment Configuration**: Environment variables for sensitive credentials and settings
- **Timezone Handling**: Brazil timezone (America/Sao_Paulo) for accurate scheduling
- **File Logging**: Persistent logging for debugging and monitoring

## Python Libraries
- **SQLAlchemy**: Database ORM and connection management
- **python-telegram-bot**: Telegram Bot API wrapper and conversation handling
- **mercadopago**: Official Mercado Pago SDK for payment processing
- **schedule**: Task scheduling for automated reminders and maintenance
- **requests**: HTTP client for WhatsApp API communication
- **pytz**: Timezone handling for Brazilian market operations