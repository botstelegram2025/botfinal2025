const { makeWASocket, DisconnectReason, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const express = require('express');
const cors = require('cors');
const QRCode = require('qrcode');

const app = express();
app.use(cors());
app.use(express.json());

let sock;
let qrCodeData = null;
let isConnected = false;
let connectionState = 'disconnected';

async function startWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');
    
    sock = makeWASocket({
        auth: state,
        printQRInTerminal: false
    });

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr) {
            console.log('QR Code gerado, escaneie com seu WhatsApp');
            qrCodeData = await QRCode.toDataURL(qr);
            connectionState = 'qr_generated';
        }
        
        if (connection === 'close') {
            const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Conexão fechada devido a ', lastDisconnect?.error, ', reconectando ', shouldReconnect);
            
            isConnected = false;
            connectionState = 'disconnected';
            qrCodeData = null;
            
            if (shouldReconnect) {
                setTimeout(startWhatsApp, 3000);
            }
        } else if (connection === 'open') {
            console.log('WhatsApp conectado com sucesso!');
            isConnected = true;
            connectionState = 'connected';
            qrCodeData = null;
        }
    });

    sock.ev.on('creds.update', saveCreds);
}

// API Endpoints
app.get('/status', (req, res) => {
    res.json({
        success: true,
        connected: isConnected,
        state: connectionState,
        qrCode: qrCodeData
    });
});

app.get('/qr', (req, res) => {
    if (qrCodeData) {
        res.json({
            success: true,
            qrCode: qrCodeData
        });
    } else {
        res.json({
            success: false,
            message: 'QR Code não disponível'
        });
    }
});

app.post('/send-message', async (req, res) => {
    try {
        const { number, message } = req.body;
        
        if (!isConnected) {
            return res.json({
                success: false,
                error: 'WhatsApp não conectado'
            });
        }
        
        // Formatar número para WhatsApp
        let formattedNumber = number.replace(/\D/g, '');
        if (!formattedNumber.startsWith('55')) {
            formattedNumber = '55' + formattedNumber;
        }
        formattedNumber += '@s.whatsapp.net';
        
        const result = await sock.sendMessage(formattedNumber, { text: message });
        
        console.log(`Mensagem enviada para ${number}: ${message}`);
        
        res.json({
            success: true,
            messageId: result.key.id,
            response: result
        });
        
    } catch (error) {
        console.error('Erro ao enviar mensagem:', error);
        res.json({
            success: false,
            error: error.message
        });
    }
});

app.post('/disconnect', async (req, res) => {
    try {
        if (sock) {
            await sock.logout();
        }
        isConnected = false;
        connectionState = 'disconnected';
        qrCodeData = null;
        
        res.json({
            success: true,
            message: 'WhatsApp desconectado'
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

app.post('/reconnect', async (req, res) => {
    try {
        if (sock) {
            sock.end();
        }
        
        setTimeout(startWhatsApp, 1000);
        
        res.json({
            success: true,
            message: 'Tentando reconectar...'
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

const PORT = 3001;
app.listen(PORT, () => {
    console.log(`Servidor Baileys rodando na porta ${PORT}`);
    startWhatsApp();
});