import schedule
import time
import threading
import logging
from datetime import datetime, timedelta, date
import asyncio
import pytz

logger = logging.getLogger(__name__)

class SchedulerService:
    """
    - Executa checagens por minuto (horário por usuário)
    - Executa verificação de pagamentos
    - Executa marcação de vencidos
    - Envia notificações diárias aos usuários
    - Garante thread + event loop assíncrono estáveis
    """

    def __init__(self):
        self.is_running = False
        self.thread = None
        self._loop = None
        self._loop_lock = threading.Lock()
        self._tz = pytz.timezone('America/Sao_Paulo')

    # ------------------- Controle -------------------

    def start(self):
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return
        self.is_running = True

        # Jobs
        schedule.every().minute.do(self._check_reminder_times)  # horários por usuário
        schedule.every().hour.do(self._check_due_dates)         # marca vencidos
        schedule.every(2).minutes.do(self._check_pending_payments)  # pagamentos

        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        logger.info("Scheduler service started")

    def stop(self):
        self.is_running = False
        schedule.clear()
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Scheduler service stopped")

    def _run_scheduler(self):
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(1)  # conferir a cada segundo o pending
            except Exception as e:
                logger.error(f"Error in scheduler: {e}", exc_info=True)

    # ------------------- Infra assíncrona -------------------

    def _get_event_loop(self) -> asyncio.AbstractEventLoop:
        """
        Retorna um event loop thread-safe. Cria se não existir.
        """
        with self._loop_lock:
            if self._loop and not self._loop.is_closed():
                return self._loop
            self._loop = asyncio.new_event_loop()
            t = threading.Thread(target=self._loop.run_forever, daemon=True)
            t.start()
            return self._loop

    def _run_coro_blocking(self, coro, timeout=60):
        """
        Agenda uma coroutine no loop e aguarda resultado de forma síncrona.
        """
        loop = self._get_event_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    # ------------------- Lógica de agendamento -------------------

    def _check_reminder_times(self):
        """
        Verifica horários configurados por usuário:
        - Envia lembretes (2 dias, 1 dia, hoje e 1 dia após) no horário do usuário
        - Envia relatório/alerta diário no horário do usuário
        Usa flags last_morning_run/last_report_run para evitar duplicidade diária.
        """
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings

            now = datetime.now(self._tz)
            now_date = now.date()
            now_hhmm = now.strftime("%H:%M")

            db = DatabaseService()
            with db.get_session() as session:
                rows = session.query(User, UserScheduleSettings).join(
                    UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True
                ).filter(User.is_active.is_(True)).all()

                logger.info(f"[{now_hhmm}] Checking reminder times for {len(rows)} users")

                for user, settings in rows:
                    # Cria defaults se não existir
                    if not settings:
                        settings = UserScheduleSettings(
                            user_id=user.id,
                            morning_reminder_time='09:00',
                            daily_report_time='08:00',
                            auto_send_enabled=True
                        )
                        session.add(settings)
                        session.commit()

                    # Se usuário desativou auto envio, só pula os lembretes
                    try:
                        if hasattr(settings, 'auto_send_enabled') and not settings.auto_send_enabled:
                            send_reminders = False
                        else:
                            send_reminders = True
                    except Exception:
                        send_reminders = True

                    # Lembretes no horário do usuário
                    if send_reminders:
                        try:
                            reminder_time = datetime.strptime(settings.morning_reminder_time or "09:00", "%H:%M").time()
                        except Exception:
                            reminder_time = datetime.strptime("09:00", "%H:%M").time()

                        last_run = getattr(settings, 'last_morning_run', None)
                        if now.time() >= reminder_time and (last_run != now_date):
                            logger.info(f"→ Daily reminders for user={user.id} (time {settings.morning_reminder_time}, now {now_hhmm})")
                            try:
                                # CHAMADA CORRETA: este método deve existir
                                self._run_coro_blocking(self._process_daily_reminders_for_user(user.id), timeout=120)
                                # Atualiza flag
                                settings.last_morning_run = now_date
                                session.commit()
                            except Exception as e:
                                logger.error(f"Error processing daily reminders for user {user.id}: {e}", exc_info=True)

                    # Relatório no horário do usuário
                    try:
                        report_time = datetime.strptime(settings.daily_report_time or "08:00", "%H:%M").time()
                    except Exception:
                        report_time = datetime.strptime("08:00", "%H:%M").time()

                    last_report = getattr(settings, 'last_report_run', None)
                    if now.time() >= report_time and (last_report != now_date):
                        logger.info(f"→ Daily report for user={user.id} (time {settings.daily_report_time}, now {now_hhmm})")
                        try:
                            self._run_coro_blocking(self._process_user_notifications_for_user(user.id), timeout=120)
                            settings.last_report_run = now_date
                            session.commit()
                        except Exception as e:
                            logger.error(f"Error processing daily report for user {user.id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error checking reminder times: {e}", exc_info=True)

    def _check_pending_payments(self):
        """
        Percorre assinaturas pendentes (< 24h), verifica status no gateway
        e atualiza usuário/assinatura em caso de approved.
        """
        logger.info("🔍 Checking pending payments")
        try:
            from services.database_service import DatabaseService
            from services.payment_service import payment_service
            from services.telegram_service import telegram_service
            from models import User, Subscription

            db = DatabaseService()
            with db.get_session() as session:
                since = datetime.utcnow() - timedelta(hours=24)
                pendings = session.query(Subscription).filter(
                    Subscription.status == 'pending',
                    Subscription.created_at >= since
                ).all()

                approved = 0
                for sub in pendings:
                    st = payment_service.check_payment_status(sub.payment_id)
                    if not st.get('success'):
                        logger.warning(f"Payment {sub.payment_id} check failed: {st.get('error')}")
                        continue

                    status = st.get('status')
                    if status == 'approved':
                        approved += 1
                        sub.status = 'approved'
                        sub.paid_at = datetime.utcnow()
                        sub.expires_at = datetime.utcnow() + timedelta(days=30)
                        user = session.get(User, sub.user_id)
                        if user:
                            user.is_trial = False
                            user.is_active = True
                            user.last_payment_date = datetime.utcnow()
                            user.next_due_date = sub.expires_at
                        session.commit()
                        # avisa no telegram (não bloqueia)
                        msg = (
                            f"✅ *Pagamento aprovado!*\n\n"
                            f"Valor: R$ {sub.amount:.2f}\n"
                            f"Próximo vencimento: {sub.expires_at.strftime('%d/%m/%Y')}"
                        )
                        try:
                            self._run_coro_blocking(telegram_service.send_message(user.telegram_id, msg), timeout=15)
                        except Exception as e:
                            logger.error(f"Notify approved failed: {e}")
                logger.info(f"Pending payments: {len(pendings)} | approved: {approved}")

                # expira muito antigos
                old = session.query(Subscription).filter(
                    Subscription.status == 'pending',
                    Subscription.created_at < since
                ).all()
                for sub in old:
                    sub.status = 'expired'
                if old:
                    session.commit()
                    logger.info(f"Expired old pendings: {len(old)}")

        except Exception as e:
            logger.error(f"Error checking pending payments: {e}", exc_info=True)

    def _check_due_dates(self):
        """
        Marca clientes vencidos como inativos.
        """
        try:
            from services.database_service import DatabaseService
            from models import Client

            db = DatabaseService()
            with db.get_session() as session:
                today = date.today()
                overdue = session.query(Client).filter(
                    Client.due_date < today,
                    Client.status == 'active'
                ).all()
                for c in overdue:
                    c.status = 'inactive'
                if overdue:
                    session.commit()
                    logger.info(f"Marked inactive: {len(overdue)} clients")
        except Exception as e:
            logger.error(f"Error checking due dates: {e}", exc_info=True)

    # ------------------- Corotinas (DEVEM EXISTIR) -------------------

    async def _process_daily_reminders_for_user(self, user_id: int):
        """
        Envia para UM usuário todos os lembretes do dia:
        - D+2, D+1, D0, D-1 (após vencimento)
        Reaproveita _send_reminders_by_type.
        """
        from services.database_service import DatabaseService
        from services.whatsapp_service import whatsapp_service
        from models import User, Client

        db = DatabaseService()
        today = date.today()

        def q(session, delta, status='active'):
            return session.query(Client).filter_by(
                user_id=user_id,
                status=status,
            ).filter(Client.due_date == (today + timedelta(days=delta))).all()

        try:
            with db.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    logger.info(f"User {user_id} not found/ inactive")
                    return

                c2 = q(session, 2)
                c1 = q(session, 1)
                c0 = q(session, 0)
                c_1 = session.query(Client).filter_by(
                    user_id=user_id,
                    status='active'
                ).filter(Client.due_date == (today - timedelta(days=1))).all()

                if c2:
                    await self._send_reminders_by_type(session, user, c2, 'reminder_2_days', whatsapp_service)
                if c1:
                    await self._send_reminders_by_type(session, user, c1, 'reminder_1_day', whatsapp_service)
                if c0:
                    await self._send_reminders_by_type(session, user, c0, 'reminder_due_date', whatsapp_service)
                if c_1:
                    await self._send_reminders_by_type(session, user, c_1, 'reminder_overdue', whatsapp_service)

        except Exception as e:
            logger.error(f"Error processing daily reminders for user {user_id}: {e}", exc_info=True)

    async def _process_user_notifications_for_user(self, user_id: int):
        """
        Monta e envia o relatório diário (overdue, hoje, +1, +2) para UM usuário via Telegram.
        """
        from services.database_service import DatabaseService
        from services.telegram_service import telegram_service
        from models import User, Client

        db = DatabaseService()
        today = date.today()
        t1 = today + timedelta(days=1)
        t2 = today + timedelta(days=2)

        try:
            with db.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    return
                clis = session.query(Client).filter_by(user_id=user.id).all()
                overdue = [c for c in clis if c.due_date < today and c.status == 'active']
                d0 = [c for c in clis if c.due_date == today and c.status == 'active']
                d1 = [c for c in clis if c.due_date == t1 and c.status == 'active']
                d2 = [c for c in clis if c.due_date == t2 and c.status == 'active']
                if not (overdue or d0 or d1 or d2):
                    return
                text = self._build_notification_message(overdue, d0, d1, d2)
                await telegram_service.send_notification(user.telegram_id, text)
        except Exception as e:
            logger.error(f"Error processing daily notifications for user {user_id}: {e}", exc_info=True)

    # ------------------- Auxiliares de envio -------------------

    def _build_notification_message(self, overdue, due_today, due_tomorrow, due_day_after):
        msg = "📅 *Relatório Diário de Vencimentos*\n\n"
        def price(c): 
            try: return f"R$ {float(c.plan_price or 0):.2f}".replace('.', ',')
            except: return "N/A"
        if overdue:
            msg += f"🔴 *{len(overdue)} em atraso:*\n"
            for c in overdue[:5]:
                days = (date.today() - c.due_date).days
                msg += f"• {c.name} - {days} dia(s)\n"
            if len(overdue) > 5: msg += f"• … e mais {len(overdue)-5}\n"
            msg += "\n"
        if due_today:
            msg += f"🟡 *{len(due_today)} vencem hoje:*\n"
            for c in due_today[:5]: msg += f"• {c.name} - {price(c)}\n"
            if len(due_today) > 5: msg += f"• … e mais {len(due_today)-5}\n"
            msg += "\n"
        if due_tomorrow:
            msg += f"🟠 *{len(due_tomorrow)} vencem amanhã:*\n"
            for c in due_tomorrow[:5]: msg += f"• {c.name} - {price(c)}\n"
            if len(due_tomorrow) > 5: msg += f"• … e mais {len(due_tomorrow)-5}\n"
            msg += "\n"
        if due_day_after:
            msg += f"🔵 *{len(due_day_after)} vencem em 2 dias:*\n"
            for c in due_day_after[:5]: msg += f"• {c.name} - {price(c)}\n"
            if len(due_day_after) > 5: msg += f"• … e mais {len(due_day_after)-5}\n"
            msg += "\n"
        msg += "📱 Use *👥 Clientes* para gerenciar."
        return msg

    async def _send_reminders_by_type(self, session, user, clients, reminder_type, whatsapp_service):
        """
        Envia mensagens via WhatsApp usando o template do usuário para o tipo informado.
        Loga sucesso/erro por cliente.
        """
        from models import MessageTemplate, MessageLog
        from datetime import datetime as dt

        try:
            template = session.query(MessageTemplate).filter_by(
                user_id=user.id,
                template_type=reminder_type,
                is_active=True
            ).first()
            if not template:
                logger.info(f"No template for {reminder_type} user={user.id}")
                return

            today = date.today()
            for c in clients:
                # evita duplicidade diária
                exists = session.query(MessageLog).filter_by(
                    user_id=user.id, client_id=c.id, template_id=template.id, status='sent'
                ).filter(MessageLog.sent_at >= dt.combine(today, dt.min.time())).first()
                if exists:
                    continue

                content = self._fill_template(template.content, c)
                res = whatsapp_service.send_message(c.phone_number, content)
                ok = bool(res.get('success'))
                log = MessageLog(
                    user_id=user.id,
                    client_id=c.id,
                    template_id=template.id,
                    message_content=content,
                    recipient_phone=c.phone_number,
                    sent_at=dt.utcnow(),
                    status='sent' if ok else 'failed',
                    error_message=None if ok else (res.get('error') or 'send failed')
                )
                session.add(log)

            session.commit()
        except Exception as e:
            logger.error(f"Error sending '{reminder_type}' reminders: {e}", exc_info=True)

    def _fill_template(self, tpl: str, c) -> str:
        def as_money(v):
            try: return f"{float(v or 0):.2f}".replace('.', ',')
            except: return "0,00"
        rep = {
            '{nome}': c.name or 'Cliente',
            '{plano}': (getattr(c, 'plan_name', None) or 'Plano'),
            '{valor}': as_money(getattr(c, 'plan_price', 0)),
            '{vencimento}': (c.due_date.strftime('%d/%m/%Y') if getattr(c, 'due_date', None) else ''),
            '{servidor}': getattr(c, 'server', None) or '—',
            '{informacoes_extras}': getattr(c, 'other_info', None) or ''
        }
        out = (tpl or '').strip()
        for k, v in rep.items():
            out = out.replace(k, str(v))
        while '\n\n\n' in out:
            out = out.replace('\n\n\n', '\n\n')
        return out.strip()


# Instância global
scheduler_service = SchedulerService()
