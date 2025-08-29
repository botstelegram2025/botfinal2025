import schedule
import time as _time
import threading
import logging
import asyncio
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Serviço de agendamento responsável por:
      - Rodar verificações minutais/horárias
      - Disparar rotinas assíncronas em um event loop dedicado
      - Respeitar horários por usuário (com execução retroativa no mesmo dia)
    """

    def __init__(self):
        self.is_running = False
        self.thread: threading.Thread | None = None

        # Loop assíncrono dedicado + thread
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_thread: threading.Thread | None = None

    # -------------------------------------------------------------------------
    # Ciclo de vida
    # -------------------------------------------------------------------------
    def start(self):
        """Inicia o serviço de agendamento e o event loop assíncrono dedicado."""
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return

        # inicia event loop dedicado em uma thread separada
        def _run_loop(loop: asyncio.AbstractEventLoop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(
            target=_run_loop, args=(self._async_loop,), daemon=True
        )
        self._async_thread.start()

        self.is_running = True

        # Agenda tarefas
        schedule.every().minute.do(self._check_reminder_times)
        schedule.every().hour.do(self._check_due_dates)
        schedule.every(2).minutes.do(self._check_pending_payments)

        # Thread do scheduler (verifica pendências com precisão de 1s)
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()

        logger.info("Scheduler service started")

    def stop(self):
        """Interrompe o serviço de agendamento e o event loop dedicado."""
        self.is_running = False
        schedule.clear()
        if self.thread:
            self.thread.join()

        # Para o loop assíncrono dedicado
        if self._async_loop and self._async_loop.is_running():
            try:
                self._async_loop.call_soon_threadsafe(self._async_loop.stop)
            except Exception as e:
                logger.warning(f"Error stopping async loop: {e}")

        if self._async_thread:
            self._async_thread.join()

        logger.info("Scheduler service stopped")

    # -------------------------------------------------------------------------
    # Infra do scheduler
    # -------------------------------------------------------------------------
    def _run_scheduler(self):
        """Executa o scheduler numa thread separada, com polling de 1 segundo."""
        while self.is_running:
            try:
                schedule.run_pending()
                _time.sleep(1)  # maior precisão: dispara próximo do minuto exato
            except Exception as e:
                logger.error(f"Error in scheduler: {e}")

    def _get_event_loop(self) -> asyncio.AbstractEventLoop:
        """Retorna o event loop dedicado; cria se necessário."""
        if self._async_loop is None:
            # fallback (não deve ocorrer se start() foi chamado)
            self._async_loop = asyncio.new_event_loop()

            def _run_loop(loop: asyncio.AbstractEventLoop):
                asyncio.set_event_loop(loop)
                loop.run_forever()

            self._async_thread = threading.Thread(
                target=_run_loop, args=(self._async_loop,), daemon=True
            )
            self._async_thread.start()

        return self._async_loop

    # -------------------------------------------------------------------------
    # Tarefas agendadas
    # -------------------------------------------------------------------------
    def _check_reminder_times(self):
        """
        Verifica se é hora de enviar lembretes/relatórios por usuário.
        - Respeita horários definidos por usuário (HH:MM)
        - Se o serviço caiu e voltou depois do horário, executa retroativamente
          (desde que ainda seja o mesmo dia)
        """
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings
            import pytz

            db_service = DatabaseService()

            # TZ Brasil
            brazil_tz = pytz.timezone("America/Sao_Paulo")
            current_datetime = datetime.now(brazil_tz)
            current_time_str = current_datetime.strftime("%H:%M")
            current_date = current_datetime.date()
            current_time = current_datetime.time()

            logger.info(f"Checking reminder times at {current_time_str}")

            with db_service.get_session() as session:
                # Usuários ativos e suas configs (outer join permite criar defaults)
                users_settings = (
                    session.query(User, UserScheduleSettings)
                    .join(UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True)
                    .filter(User.is_active.is_(True))
                    .all()
                )

                logger.info(f"Found {len(users_settings)} users to check")

                for user, settings in users_settings:
                    # Trial expirado?
                    self._check_trial_expiration(user, current_date)

                    # Cria defaults se não existir config
                    if not settings:
                        logger.info(f"Creating default settings for user {user.id}")
                        settings = UserScheduleSettings(
                            user_id=user.id,
                            morning_reminder_time="09:00",
                            daily_report_time="08:00",
                            auto_send_enabled=True,
                        )
                        session.add(settings)
                        session.commit()

                    if getattr(settings, "auto_send_enabled", True) is False:
                        logger.info(f"Auto send disabled for user {user.id}, skipping")
                        continue

                    logger.info(
                        f"Checking times for user {user.id}: "
                        f"morning={settings.morning_reminder_time}, report={settings.daily_report_time}"
                    )

                    # --- Lembretes diários (morning) ---
                    try:
                        daily_time = datetime.strptime(settings.morning_reminder_time, "%H:%M").time()
                    except ValueError as e:
                        logger.error(f"Invalid morning time for user {user.id}: {e}")
                        daily_time = None

                    last_run = getattr(settings, "last_morning_run", None)

                    if daily_time and (current_time >= daily_time) and (last_run != current_date):
                        logger.info(
                            f"Processing daily reminders for user {user.id} "
                            f"(time passed: {current_time_str} >= {settings.morning_reminder_time})"
                        )
                        try:
                            fut = asyncio.run_coroutine_threadsafe(
                                self._process_daily_reminders_for_user(user.id),
                                self._get_event_loop(),
                            )
                            fut.result(timeout=60)

                            # Atualiza a data da última execução
                            user_settings = (
                                session.query(UserScheduleSettings).filter_by(user_id=user.id).first()
                            )
                            if user_settings:
                                user_settings.last_morning_run = current_date
                                session.commit()

                            logger.info(f"Daily reminders completed for user {user.id}")
                        except Exception as e:
                            logger.error(f"Error processing daily reminders for user {user.id}: {e}")

                    # --- Relatório diário ---
                    try:
                        report_time = datetime.strptime(settings.daily_report_time, "%H:%M").time()
                    except ValueError as e:
                        logger.error(f"Invalid report time for user {user.id}: {e}")
                        report_time = None

                    last_report_run = getattr(settings, "last_report_run", None)

                    if report_time and (current_time >= report_time) and (last_report_run != current_date):
                        logger.info(
                            f"Processing daily report for user {user.id} "
                            f"(time passed: {current_time_str} >= {settings.daily_report_time})"
                        )
                        try:
                            fut = asyncio.run_coroutine_threadsafe(
                                self._process_user_notifications_for_user(user.id),
                                self._get_event_loop(),
                            )
                            fut.result(timeout=60)

                            # Atualiza a data da última execução
                            user_settings = (
                                session.query(UserScheduleSettings).filter_by(user_id=user.id).first()
                            )
                            if user_settings:
                                user_settings.last_report_run = current_date
                                session.commit()

                            logger.info(f"Daily report completed for user {user.id}")
                        except Exception as e:
                            logger.error(f"Error processing daily report for user {user.id}: {e}")

        except Exception as e:
            logger.error(f"Error checking reminder times: {e}")

    def _check_pending_payments(self):
        """Verifica pagamentos pendentes e processa aprovações automaticamente."""
        logger.info("🔍 Checking pending payments for automatic processing")
        try:
            from services.database_service import DatabaseService
            from services.payment_service import payment_service
            from services.telegram_service import telegram_service
            from models import User, Subscription

            db_service = DatabaseService()

            with db_service.get_session() as session:
                yesterday = datetime.utcnow() - timedelta(hours=24)
                pending_subscriptions = (
                    session.query(Subscription)
                    .filter(
                        Subscription.status == "pending",
                        Subscription.created_at >= yesterday,
                    )
                    .all()
                )

                logger.info(f"📋 Found {len(pending_subscriptions)} pending payments to check")

                approved_count = 0
                pending_count = 0

                for subscription in pending_subscriptions:
                    logger.info(f"🔍 Checking payment {subscription.payment_id} for user {subscription.user_id}")

                    payment_status = payment_service.check_payment_status(subscription.payment_id)

                    if payment_status.get("success"):
                        current_status = payment_status.get("status")
                        status_detail = payment_status.get("status_detail", "N/A")
                        logger.info(
                            f"📊 Payment {subscription.payment_id} status: {current_status} ({status_detail})"
                        )

                        if current_status == "approved":
                            approved_count += 1
                            logger.info(f"✅ Payment {subscription.payment_id} APPROVED! Processing automatically...")

                            old_status = subscription.status
                            subscription.status = "approved"
                            subscription.paid_at = datetime.utcnow()
                            subscription.expires_at = datetime.utcnow() + timedelta(days=30)

                            user = session.query(User).get(subscription.user_id)
                            if user:
                                user.is_trial = False
                                user.is_active = True
                                user.last_payment_date = datetime.utcnow()
                                user.next_due_date = subscription.expires_at

                                # Notificação assíncrona via Telegram
                                try:
                                    notification_message = (
                                        f"✅ **PAGAMENTO APROVADO AUTOMATICAMENTE!**\n\n"
                                        f"💰 **Valor:** R$ {subscription.amount:.2f}\n"
                                        f"📅 **Aprovado em:** {datetime.now().strftime('%d/%m/%Y às %H:%M')}\n\n"
                                        f"🎉 **Sua conta foi ativada!**\n"
                                        f"• Plano Premium ativo por 30 dias\n"
                                        f"• Todos os recursos liberados\n"
                                        f"• Próximo vencimento: {subscription.expires_at.strftime('%d/%m/%Y')}\n\n"
                                        f"🚀 Use o comando /start para acessar todas as funcionalidades!"
                                    )

                                    fut = asyncio.run_coroutine_threadsafe(
                                        telegram_service.send_message(user.telegram_id, notification_message),
                                        self._get_event_loop(),
                                    )
                                    fut.result(timeout=20)
                                    logger.info(f"📲 Automatic approval notification sent to user {user.telegram_id}")
                                except Exception as e:
                                    logger.error(f"❌ Error sending approval notification: {e}")

                                logger.info(f"✅ User {user.telegram_id} account AUTOMATICALLY ACTIVATED!")

                            session.commit()
                            logger.info(
                                f"💾 Payment {subscription.payment_id} updated: {old_status} → approved"
                            )

                        elif current_status == "pending":
                            pending_count += 1
                            if status_detail == "pending_waiting_transfer":
                                logger.info(
                                    f"⏳ Payment {subscription.payment_id} - User hasn't scanned PIX code yet"
                                )
                            else:
                                logger.info(
                                    f"⏳ Payment {subscription.payment_id} - Still processing: {status_detail}"
                                )

                        elif current_status in ["rejected", "cancelled"]:
                            logger.info(
                                f"❌ Payment {subscription.payment_id} {current_status} - updating status"
                            )
                            subscription.status = current_status
                            session.commit()

                    else:
                        logger.warning(
                            f"⚠️ Failed to check payment {subscription.payment_id}: {payment_status.get('error')}"
                        )

                if len(pending_subscriptions) > 0:
                    logger.info(
                        f"📊 Payment check summary: {approved_count} approved, "
                        f"{pending_count} still pending, "
                        f"{len(pending_subscriptions) - approved_count - pending_count} other status"
                    )

                # Limpeza de pendentes muito antigos (> 24h)
                old_pending = (
                    session.query(Subscription)
                    .filter(
                        Subscription.status == "pending",
                        Subscription.created_at < yesterday,
                    )
                    .all()
                )
                for old_sub in old_pending:
                    old_sub.status = "expired"
                    logger.info(f"⏰ Expired old pending payment {old_sub.payment_id}")

                if old_pending:
                    session.commit()
                    logger.info(f"🧹 Cleaned up {len(old_pending)} expired payments")

        except Exception as e:
            logger.error(f"❌ Error checking pending payments: {e}")
            import traceback

            logger.error(traceback.format_exc())

    def _check_due_dates(self):
        """Marca clientes vencidos como inativos."""
        logger.info("Running due date check")
        try:
            from services.database_service import DatabaseService
            from models import Client

            db_service = DatabaseService()

            with db_service.get_session() as session:
                today = date.today()
                overdue_clients = (
                    session.query(Client)
                    .filter(Client.due_date < today, Client.status == "active")
                    .all()
                )

                for client in overdue_clients:
                    client.status = "inactive"
                    logger.info(f"Marked client {client.name} as inactive (overdue)")

                session.commit()
        except Exception as e:
            logger.error(f"Error checking due dates: {e}")

    # -------------------------------------------------------------------------
    # Notificações diárias (assíncronas)
    # -------------------------------------------------------------------------
    async def _process_user_notifications(self):
        """Processa e envia notificações diárias aos usuários via Telegram."""
        from services.database_service import DatabaseService
        from services.telegram_service import telegram_service
        from models import Client, User

        db_service = DatabaseService()

        today = date.today()
        tomorrow = today + timedelta(days=1)
        day_after_tomorrow = today + timedelta(days=2)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()

                for user in users:
                    overdue_clients = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status="active")
                        .filter(Client.due_date < today)
                        .all()
                    )
                    due_today = (
                        session.query(Client)
                        .filter_by(user_id=user.id, due_date=today, status="active")
                        .all()
                    )
                    due_tomorrow = (
                        session.query(Client)
                        .filter_by(user_id=user.id, due_date=tomorrow, status="active")
                        .all()
                    )
                    due_day_after = (
                        session.query(Client)
                        .filter_by(user_id=user.id, due_date=day_after_tomorrow, status="active")
                        .all()
                    )

                    if overdue_clients or due_today or due_tomorrow or due_day_after:
                        notification_text = self._build_notification_message(
                            overdue_clients, due_today, due_tomorrow, due_day_after
                        )
                        success = await telegram_service.send_notification(
                            user.telegram_id, notification_text
                        )
                        if success:
                            logger.info(f"Sent daily notification to user {user.telegram_id}")
                        else:
                            logger.error(f"Failed to send notification to user {user.telegram_id}")

        except Exception as e:
            logger.error(f"Error processing user notifications: {e}")

    def _build_notification_message(self, overdue_clients, due_today, due_tomorrow, due_day_after):
        """Monta o texto de notificação diária para o usuário."""
        message = "📅 **Relatório Diário de Vencimentos**\n\n"

        if overdue_clients:
            message += f"🔴 **{len(overdue_clients)} cliente(s) em atraso:**\n"
            for client in overdue_clients[:5]:
                days_overdue = (date.today() - client.due_date).days
                message += f"• {client.name} - {days_overdue} dia(s) de atraso\n"
            if len(overdue_clients) > 5:
                message += f"• ... e mais {len(overdue_clients) - 5} cliente(s)\n"
            message += "\n"

        if due_today:
            message += f"🟡 **{len(due_today)} cliente(s) vencem hoje:**\n"
            for client in due_today[:5]:
                message += f"• {client.name} - R$ {client.plan_price:.2f}\n"
            if len(due_today) > 5:
                message += f"• ... e mais {len(due_today) - 5} cliente(s)\n"
            message += "\n"

        if due_tomorrow:
            message += f"🟠 **{len(due_tomorrow)} cliente(s) vencem amanhã:**\n"
            for client in due_tomorrow[:5]:
                message += f"• {client.name} - R$ {client.plan_price:.2f}\n"
            if len(due_tomorrow) > 5:
                message += f"• ... e mais {len(due_tomorrow) - 5} cliente(s)\n"
            message += "\n"

        if due_day_after:
            message += f"🔵 **{len(due_day_after)} cliente(s) vencem em 2 dias:**\n"
            for client in due_day_after[:5]:
                message += f"• {client.name} - R$ {client.plan_price:.2f}\n"
            if len(due_day_after) > 5:
                message += f"• ... e mais {len(due_day_after) - 5} cliente(s)\n"
            message += "\n"

        message += "📱 Use o menu **👥 Clientes** para gerenciar seus clientes."
        return message

    # -------------------------------------------------------------------------
    # Lembretes (assíncronos)
    # -------------------------------------------------------------------------
    async def _process_reminders(self):
        """Processa e envia mensagens de lembrete."""
        from services.database_service import DatabaseService
        from services.whatsapp_service import WhatsAppService
        from models import Client, User

        db_service = DatabaseService()
        whatsapp_service = WhatsAppService()

        today = date.today()
        reminder_2_days = today + timedelta(days=2)
        reminder_1_day = today + timedelta(days=1)
        overdue_date = today - timedelta(days=1)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()

                for user in users:
                    await self._send_reminder_type(session, user, today, "reminder_due_date", whatsapp_service)
                    await self._send_reminder_type(session, user, reminder_1_day, "reminder_1_day", whatsapp_service)
                    await self._send_reminder_type(session, user, reminder_2_days, "reminder_2_days", whatsapp_service)
                    await self._send_reminder_type(session, user, overdue_date, "reminder_overdue", whatsapp_service)

        except Exception as e:
            logger.error(f"Error processing reminders: {e}")

    async def _process_evening_reminders(self):
        """Processa lembretes da noite (para o dia seguinte)."""
        from services.database_service import DatabaseService
        from services.whatsapp_service import WhatsAppService
        from models import User

        db_service = DatabaseService()
        whatsapp_service = WhatsAppService()

        tomorrow = date.today() + timedelta(days=1)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()
                for user in users:
                    await self._send_reminder_type(session, user, tomorrow, "reminder_1_day", whatsapp_service)
        except Exception as e:
            logger.error(f"Error processing evening reminders: {e}")

    async def _send_reminder_type(self, session, user, target_date, reminder_type, whatsapp_service):
        """Envia um tipo específico de lembrete."""
        from models import Client, MessageTemplate, MessageLog

        try:
            template = (
                session.query(MessageTemplate)
                .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
                .first()
            )
            if not template:
                logger.warning(f"No template found for {reminder_type} for user {user.id}")
                return

            clients = (
                session.query(Client)
                .filter_by(user_id=user.id, due_date=target_date, status="active", auto_reminders_enabled=True)
                .all()
            )

            for client in clients:
                existing_log = (
                    session.query(MessageLog)
                    .filter_by(user_id=user.id, client_id=client.id, template_id=template.id)
                    .filter(MessageLog.sent_at >= datetime.combine(date.today(), datetime.min.time()))
                    .first()
                )
                if existing_log:
                    logger.info(
                        f"Message already sent today for client {client.name}, type {reminder_type}"
                    )
                    continue

                message_content = self._replace_template_variables(template.content, client)
                result = whatsapp_service.send_message(client.phone_number, message_content, user.id)

                if result.get("success"):
                    message_log = MessageLog(
                        user_id=user.id,
                        client_id=client.id,
                        template_id=template.id,
                        message_content=message_content,
                        sent_at=datetime.now(),
                        status="sent",
                    )
                    session.add(message_log)
                    logger.info(f"Sent {reminder_type} reminder to {client.name}")
                else:
                    message_log = MessageLog(
                        user_id=user.id,
                        client_id=client.id,
                        template_id=template.id,
                        message_content=message_content,
                        sent_at=datetime.now(),
                        status="failed",
                    )
                    session.add(message_log)
                    logger.error(f"Failed to send {reminder_type} reminder to {client.name}")

            session.commit()

        except Exception as e:
            logger.error(f"Error sending {reminder_type} reminders: {e}")

    def _replace_template_variables(self, template_content, client):
        """Substitui variáveis do template pelos dados do cliente."""
        variables = {
            "{nome}": client.name,
            "{plano}": client.plan_name,
            "{valor}": f"{client.plan_price:.2f}",
            "{vencimento}": client.due_date.strftime("%d/%m/%Y"),
            "{servidor}": client.server or "Não definido",
            "{informacoes_extras}": client.other_info or "",
        }

        result = template_content
        for var, value in variables.items():
            result = result.replace(var, str(value))

        if not client.other_info:
            result = result.replace("\n\n\n", "\n\n")

        return result.strip()

    async def _send_reminders_by_type(self, session, user, clients, reminder_type, whatsapp_service):
        """Envia lembretes a clientes de um tipo específico (variante com template_type)."""
        from models import MessageTemplate, MessageLog

        try:
            template = (
                session.query(MessageTemplate)
                .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
                .first()
            )
            if not template:
                logger.warning(f"No template found for {reminder_type} for user {user.id}")
                return

            for client in clients:
                existing_log = (
                    session.query(MessageLog)
                    .filter_by(user_id=user.id, client_id=client.id, template_type=reminder_type)
                    .filter(MessageLog.sent_at >= datetime.combine(date.today(), datetime.min.time()))
                    .first()
                )
                if existing_log:
                    logger.info(
                        f"Message already sent today for client {client.name}, type {reminder_type}"
                    )
                    continue

                message_content = self._replace_template_variables(template.content, client)
                result = whatsapp_service.send_message(client.phone_number, message_content, user.id)

                if result.get("success"):
                    message_log = MessageLog(
                        user_id=user.id,
                        client_id=client.id,
                        template_type=reminder_type,
                        recipient_phone=client.phone_number,
                        message_content=message_content,
                        sent_at=datetime.now(),
                        status="sent",
                    )
                    session.add(message_log)
                    logger.info(
                        f"Sent {reminder_type} reminder to {client.name} ({client.phone_number})"
                    )
                else:
                    error_msg = result.get("error", "WhatsApp send failed")
                    message_log = MessageLog(
                        user_id=user.id,
                        client_id=client.id,
                        template_type=reminder_type,
                        recipient_phone=client.phone_number,
                        message_content=message_content,
                        sent_at=datetime.now(),
                        status="failed",
                        error_message=error_msg,
                    )
                    session.add(message_log)
                    logger.error(f"Failed to send {reminder_type} reminder to {client.name}: {error_msg}")

            session.commit()

        except Exception as e:
            logger.error(f"Error sending {reminder_type} reminders: {e}")

    # -------------------------------------------------------------------------
    # Notificações de trial/assinatura
    # -------------------------------------------------------------------------
    def _check_trial_expiration(self, user, current_date: date):
        """Verifica expiração de trial e dispara notificações/ação."""
        try:
            if not user.is_trial:
                return

            from datetime import timedelta as _tdelta

            trial_end_date = user.created_at.date() + _tdelta(days=7)
            days_until_expiry = (trial_end_date - current_date).days

            # Trial expirou hoje ou já expirou
            if days_until_expiry <= 0 and user.is_active:
                logger.info(f"Trial expired for user {user.id}, sending payment notification")

                from services.database_service import DatabaseService

                db_service = DatabaseService()
                with db_service.get_session() as session:
                    db_user = session.query(type(user)).filter_by(id=user.id).first()
                    if db_user:
                        db_user.is_active = False
                        session.commit()

                        fut = asyncio.run_coroutine_threadsafe(
                            self._send_payment_notification(user.telegram_id),
                            self._get_event_loop(),
                        )
                        try:
                            fut.result(timeout=20)
                        except Exception as e:
                            logger.error(f"Error sending payment notification: {e}")

            # 1 dia antes de expirar
            elif days_until_expiry == 1:
                logger.info(f"Sending trial expiry reminder for user {user.id} (1 day left)")
                fut = asyncio.run_coroutine_threadsafe(
                    self._send_trial_reminder(user.telegram_id, days_until_expiry),
                    self._get_event_loop(),
                )
                try:
                    fut.result(timeout=20)
                except Exception as e:
                    logger.error(f"Error sending trial reminder: {e}")

        except Exception as e:
            logger.error(f"Error checking trial expiration for user {user.id}: {e}")

    async def _send_payment_notification(self, telegram_id):
        """Envia notificação quando o trial expira."""
        try:
            from services.telegram_service import telegram_service

            message = (
                "⚠️ **Seu período de teste expirou!**\n\n"
                "Seu teste gratuito de 7 dias chegou ao fim. Para continuar usando todas as funcionalidades do bot, "
                "você precisa ativar a assinatura mensal.\n\n"
                "💰 **Assinatura:** R$ 20,00/mês\n"
                "✅ **Inclui:**\n"
                "• Gestão ilimitada de clientes\n"
                "• Lembretes automáticos via WhatsApp\n"
                "• Controle de vencimentos\n"
                "• Relatórios detalhados\n"
                "• Suporte prioritário\n\n"
                "🔗 Use o comando /start para assinar e reativar sua conta!"
            )

            await telegram_service.send_notification(telegram_id, message)
            logger.info(f"Payment notification sent to user {telegram_id}")

        except Exception as e:
            logger.error(f"Error sending payment notification: {e}")

    async def _send_trial_reminder(self, telegram_id, days_left: int):
        """Envia lembrete de trial prestes a expirar."""
        try:
            from services.telegram_service import telegram_service

            message = (
                f"⏰ **Lembrete: Seu teste expira em {days_left} dia(s)!**\n\n"
                "Seu período gratuito está chegando ao fim. Não perca o acesso às suas funcionalidades!\n\n"
                "💰 **Assinatura:** R$ 20,00/mês\n"
                "🎯 **Mantenha:**\n"
                "• Todos os seus clientes cadastrados\n"
                "• Lembretes automáticos configurados\n"
                "• Histórico de mensagens\n\n"
                "Para assinar e garantir a continuidade, use o comando /start quando seu teste expirar."
            )

            await telegram_service.send_notification(telegram_id, message)
            logger.info(f"Trial reminder sent to user {telegram_id}")

        except Exception as e:
            logger.error(f"Error sending trial reminder: {e}")


# Instância global
scheduler_service = SchedulerService()
