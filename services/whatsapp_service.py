# services/whatsapp_service.py
import os
import time
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)


class WhatsAppService:
    def __init__(self):
        """
        Cliente do gateway Baileys (WhatsApp).
        Prioriza WHATSAPP_SERVICE_URL; em Railway, se ausente, usa http://127.0.0.1:3001.
        """
        railway_env = os.getenv("RAILWAY_ENVIRONMENT_NAME")
        explicit_url = os.getenv("WHATSAPP_SERVICE_URL")

        if explicit_url:
            # Recomendo NÃO colocar porta aqui; deixe só https://seu-app.up.railway.app
            self.baileys_url = explicit_url.rstrip("/")
        elif railway_env:
            self.baileys_url = "http://127.0.0.1:3001"
        else:
            self.baileys_url = "http://localhost:3001"

        self.api_token: Optional[str] = os.getenv("WHATSAPP_API_TOKEN") or None

        # timeouts mais folgados p/ Railway
        self.short_timeout = int(os.getenv("WHATSAPP_HTTP_TIMEOUT_SHORT", "20"))
        self.long_timeout = int(os.getenv("WHATSAPP_HTTP_TIMEOUT_LONG", "45"))

        self.headers = {"Content-Type": "application/json"}
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"

        logger.info(f"WhatsApp Service initialized with URL: {self.baileys_url}")

    # ---------------- HTTP helper ----------------

    def _http(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        allow_404: bool = False,
    ) -> Dict[str, Any]:
        url = f"{self.baileys_url}{path}"
        to = timeout or self.short_timeout
        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=self.headers,
                json=json_body,
                timeout=to,
            )
            if allow_404 and resp.status_code == 404:
                return {"success": False, "error": "Not found", "status": 404}

            if resp.status_code >= 400:
                return {
                    "success": False,
                    "error": f"HTTP Error: {resp.status_code}",
                    "details": resp.text,
                }

            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text}

            if "success" not in data:
                data["success"] = True

            return data
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout", "details": f"timeout={to}s"}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": "Connection error", "details": str(e)}
        except Exception as e:
            return {"success": False, "error": "Unexpected error", "details": str(e)}

    # ---------------- “Aquecimento” do servidor ----------------

    def _warm_up(self, tries: int = 6, sleep_s: int = 3) -> bool:
        """
        Tenta 'acordar' o serviço no Railway:
        - GET /health (primary)
        - GET / (fallback)
        Retorna True se algum responder OK.
        """
        for i in range(1, tries + 1):
            res = self._http("GET", "/health", timeout=self.short_timeout)
            if res.get("success"):
                logger.info("Baileys /health OK")
                return True

            # fallback tenta a raiz
            res2 = self._http("GET", "/", timeout=self.short_timeout)
            if res2.get("success"):
                logger.info("Baileys root OK")
                return True

            logger.info(f"Aguardando WhatsApp server (tentativa {i}/{tries})…")
            time.sleep(sleep_s)

        logger.warning("WhatsApp server não respondeu ao aquecimento")
        return False

    # ---------------- API principal ----------------

    def send_message(self, phone_number: str, message: str, user_id: int) -> Dict[str, Any]:
        try:
            clean = "".join(filter(str.isdigit, phone_number or ""))
            if not clean:
                return {"success": False, "error": "Invalid phone"}

            if not clean.startswith("55"):
                clean = "55" + clean

            payload = {"number": clean, "message": message}

            # garante que o serviço esteja de pé antes
            self._warm_up()

            res = self._http("POST", f"/send/{user_id}", json_body=payload, timeout=self.long_timeout)
            if res.get("success"):
                logger.info(f"WhatsApp message sent to {clean}")
                return {
                    "success": True,
                    "message_id": res.get("messageId") or res.get("id"),
                    "response": res,
                }

            # se falhou por desconexão, tenta restaurar
            err_txt = (res.get("error") or "").lower()
            if "not connected" in err_txt or "não conectado" in err_txt:
                self.restore_session(user_id)

            return res
        except Exception as e:
            logger.error(f"Unexpected error sending WhatsApp message: {e}")
            return {"success": False, "error": "Unexpected error", "details": str(e)}

    def get_health_status(self) -> Dict[str, Any]:
        return self._http("GET", "/health")

    def check_instance_status(self, user_id: int) -> Dict[str, Any]:
        res = self._http("GET", f"/status/{user_id}")
        if res.get("success", False):
            return {
                "success": True,
                "connected": bool(res.get("connected")),
                "state": res.get("state", "unknown"),
                "qrCode": res.get("qrCode"),
                "response": res,
            }
        return res

    def disconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        return self._http("POST", f"/disconnect/{user_id}")

    def reconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        return self._http("POST", f"/reconnect/{user_id}", timeout=self.long_timeout)

    def restore_session(self, user_id: int) -> Dict[str, Any]:
        # alias simples para reconectar
        return self.reconnect_whatsapp(user_id)

    def request_pairing_code(self, user_id: int, phone_number: str) -> Dict[str, Any]:
        payload = {"phoneNumber": phone_number}
        res = self._http(
            "POST",
            f"/pairing-code/{user_id}",
            json_body=payload,
            timeout=self.long_timeout,
            allow_404=True,
        )
        if res.get("status") == 404:
            return {"success": False, "error": "pairing-code endpoint not available on server"}
        return {
            "success": bool(res.get("success")),
            "pairing_code": res.get("pairingCode") or res.get("code"),
            "response": res,
        }

    def get_pairing_code(self, user_id: int) -> Dict[str, Any]:
        res = self._http("GET", f"/pairing-code/{user_id}", allow_404=True)
        if res.get("status") == 404:
            return {"success": False, "error": "pairing-code endpoint not available on server"}
        return res

    def get_qr_code(self, user_id: int) -> Dict[str, Any]:
        status = self.check_instance_status(user_id)
        if status.get("success") and status.get("qrCode"):
            return {
                "success": True,
                "qrCode": status["qrCode"],
                "connected": status.get("connected", False),
                "state": status.get("state", "unknown"),
            }
        return {"success": False, "error": "QR Code not available", "details": status}

    def force_new_qr(self, user_id: int) -> Dict[str, Any]:
        """
        Fluxo robusto sem /force-qr:
        0) Aquece o servidor (evita 502).
        1) POST /disconnect/:id (best effort)
        2) POST /reconnect/:id com até 3 tentativas (backoff)
        3) Polling /status/:id por até N segundos aguardando qrCode
        """
        # 0) aquece (tenta “acordar” serviços hibernados do Railway)
        self._warm_up()

        # 1) desconecta (ignorar erro)
        try:
            self.disconnect_whatsapp(user_id)
        except Exception:
            pass

        # 2) reconectar com backoff
        attempts = 3
        sleep_s = 2
        last_err: Optional[Dict[str, Any]] = None

        for i in range(1, attempts + 1):
            rec = self.reconnect_whatsapp(user_id)
            if rec.get("success"):
                break
            last_err = rec
            logger.warning(f"reconnect falhou (tentativa {i}/{attempts}): {rec}")
            time.sleep(sleep_s)
            sleep_s *= 2  # backoff

        else:
            # esgotou tentativas
            return {
                "success": False,
                "error": "Reconnect failed",
                "details": last_err or {"reason": "unknown"},
            }

        # 3) polling por qrCode
        max_wait = int(os.getenv("WHATSAPP_FORCE_QR_TIMEOUT", "40"))
        waited = 0
        interval = 2

        while waited < max_wait:
            status = self.check_instance_status(user_id)
            if status.get("success") and status.get("qrCode"):
                return {
                    "success": True,
                    "qrCode": status["qrCode"],
                    "state": status.get("state"),
                    "connected": status.get("connected"),
                }
            time.sleep(interval)
            waited += interval

        return {"success": False, "error": "QR Code not available after reconnect/polling"}

    def format_message(self, template: str, **kwargs) -> str:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error(f"Missing template variable: {e}")
            return template
        except Exception as e:
            logger.error(f"Error formatting message template: {e}")
            return template


whatsapp_service = WhatsAppService()
