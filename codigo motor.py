# -*- coding: utf-8 -*-
"""
Created on Sat Dec  6 11:34:46 2025

@author: adriele_scoz
"""


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aplicação Modbus TCP para controle de Inversor de Frequência (protótipo SENAI).
Biblioteca: uModbus v1.0.4

Funcionalidades:
- Ligar/Parar motor
- Definir velocidade (validação de faixa)
- Definir sentido de giro (horário/anti-horário)
- Ler temperatura, corrente, tensão
- Verificar estado do motor (RUNNING/STOPPED/FAULT)
- Iniciar com configuração padrão (30 Hz, horário)
- Menu CLI

ATENÇÃO:
- Os endereços dos registradores e escalas abaixo são EXEMPLOS/PLACEHOLDERS.
- Ajuste conforme o anexo do manual do inversor.
"""

import socket
import time
import logging
import argparse
from dataclasses import dataclass
from typing import List, Optional

from umodbus import conf
from umodbus.client import tcp

# --------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("InverterApp")

# --------- LIMITES DE VELOCIDADE (ajuste conforme manual) ----------
MIN_FREQ = 1.0   # Hz
MAX_FREQ = 60.0  # Hz

# --------- CONFIGURAÇÃO DE REGISTRADORES (PLACEHOLDER) ----------
@dataclass
class InverterRegisters:
    """
    Mapeamento de registradores do inversor.
    Ajuste conforme o manual do SENAI/inversor.
    """

    # Comandos básicos (coils ou holdings conforme equipamento)
    start_coil: int = 1100            # Endereço da coil para ligar motor (ex.: 00001)
    stop_coil: int = 1100             # Endereço da coil para parar motor (ex.: 00002)

    # Velocidade (holding register)
    speed_setpoint_reg: int = 30400  # Endereço do registrador de setpoint de velocidade
    speed_units_per_hz: int = 41400  # Ex.: 100 => 0.01 Hz por unidade (30.00 Hz -> 3000)

    # Direção (holding register: 0=horário, 1=anti-horário)
    direction_reg: int = 1101
    direction_forward_value: int = 0
    direction_reverse_value: int = 1

    # Leituras (input registers ou holdings conforme manual)
    temperature_input_reg: int = 30403  # 0.1 °C por unidade (25.3°C -> 253)
    current_input_reg: int = 30401      # 0.01 A por unidade (12.34A -> 1234)
    voltage_input_reg: int = 30402      # 0.1 V por unidade (220.0V -> 2200)

    # Status (holding register; bits: 0=running, 1=fault) - ajustar conforme manual
    status_holding_reg: int = 100


class ModbusConnection:
    """
    Gerencia a conexão TCP com o inversor via uModbus.
    """
    def __init__(self, host: str, port: int = 502, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

        # Configuração do uModbus: valores sem sinal, a menos que o manual indique o contrário
        conf.SIGNED_VALUES = False

    def connect(self):
        if self.sock:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        logger.info(f"Conectando em {self.host}:{self.port} ...")
        self.sock.connect((self.host, self.port))
        logger.info("Conectado.")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            finally:
                self.sock = None
                logger.info("Conexão fechada.")

    def send(self, message) -> List[int]:
        if not self.sock:
            self.connect()
        try:
            return tcp.send_message(message, self.sock)
        except (socket.timeout, OSError) as e:
            logger.error(f"Erro de comunicação: {e}. Tentando reconectar...")
            self.close()
            time.sleep(0.5)
            self.connect()
            return tcp.send_message(message, self.sock)


class InverterClient:
    """
    Cliente de alto nível para o inversor usando Modbus TCP.
    """
    def __init__(self, host: str, port: int, slave_id: int, regs: InverterRegisters):
        self.conn = ModbusConnection(host, port)
        self.slave_id = slave_id
        self.regs = regs

    # ---------- Comandos ----------
    def start_motor(self):
        """
        Liga o motor escrevendo True na coil de start.
        """
        msg = tcp.write_single_coil(self.slave_id, self.regs.start_coil, True)
        self.conn.send(msg)
        logger.info("Comando: LIGAR motor enviado.")

    def stop_motor(self):
        """
        Para o motor escrevendo True na coil de stop (ou False na start, conforme manual).
        """
        msg = tcp.write_single_coil(self.slave_id, self.regs.stop_coil, False)
        self.conn.send(msg)
        logger.info("Comando: PARAR motor enviado.")

    def set_speed(self, freq_hz: float):
        """
        Define a velocidade em Hz, validando faixa e convertendo para unidades do registrador.
        """
        if freq_hz < MIN_FREQ or freq_hz > MAX_FREQ:
            raise ValueError(f"Velocidade inválida: {freq_hz} Hz. "
                             f"Faixa permitida: {MIN_FREQ}–{MAX_FREQ} Hz.")

        value_units = int(round(freq_hz * self.regs.speed_units_per_hz))
        msg = tcp.write_single_register(self.slave_id, self.regs.speed_setpoint_reg, value_units)
        self.conn.send(msg)
        logger.info(f"Velocidade definida: {freq_hz:.2f} Hz (registrador={value_units}).")

    def set_direction(self, forward: bool = True):
        """
        Define sentido de giro. True=horário (forward), False=anti-horário.
        """
        val = self.regs.direction_forward_value if forward else self.regs.direction_reverse_value
        msg = tcp.write_single_register(self.slave_id, self.regs.direction_reg, val)
        self.conn.send(msg)
        logger.info(f"Sentido de giro: {'horário' if forward else 'anti-horário'}.")

    # ---------- Leituras ----------
    def read_temperature_c(self) -> float:
        """
        Lê temperatura (°C). Ajuste o tipo de registrador conforme manual (input/holding).
        """
        msg = tcp.read_input_registers(self.slave_id, self.regs.temperature_input_reg, 1)
        resp = self.conn.send(msg)
        raw = resp[0]
        temp_c = raw / 10.0  # escala placeholder: 0.1 °C/unidade
        logger.info(f"Temperatura: {temp_c:.1f} °C")
        return temp_c

    def read_current_a(self) -> float:
        """
        Lê corrente (A).
        """
        msg = tcp.read_input_registers(self.slave_id, self.regs.current_input_reg, 1)
        resp = self.conn.send(msg)
        raw = resp[0]
        current_a = raw / 100.0  # escala placeholder: 0.01 A/unidade
        logger.info(f"Corrente: {current_a:.2f} A")
        return current_a

    def read_voltage_v(self) -> float:
        """
        Lê tensão (V).
        """
        msg = tcp.read_input_registers(self.slave_id, self.regs.voltage_input_reg, 1)
        resp = self.conn.send(msg)
        raw = resp[0]
        voltage_v = raw / 10.0  # escala placeholder: 0.1 V/unidade
        logger.info(f"Tensão: {voltage_v:.1f} V")
        return voltage_v

    def read_status(self) -> dict:
        """
        Lê registrador de status e interpreta bits (placeholder).
        bit0 = RUNNING (1=rodando)
        bit1 = FAULT   (1=falha)
        """
        msg = tcp.read_holding_registers(self.slave_id, self.regs.status_holding_reg, 1)
        resp = self.conn.send(msg)
        status_reg = resp[0]

        running = bool(status_reg & 0b0001)
        fault   = bool(status_reg & 0b0010)

        state = "RUNNING" if running and not fault else "FAULT" if fault else "STOPPED"

        info = {"raw": status_reg, "running": running, "fault": fault, "state": state}
        logger.info(f"Estado do motor: {state} (raw=0x{status_reg:04X})")
        return info

    # ---------- Fluxo padrão ----------
    def start_with_defaults(self):
        """
        Inicia motor com: 30 Hz e sentido horário.
        """
        self.set_direction(forward=True)
        self.set_speed(30.0)
        self.start_motor()
        logger.info("Inicialização padrão aplicada (30 Hz, horário).")


# ---------------- CLI / MENU ----------------
def interactive_menu(client: InverterClient):
    """
    Menu interativo simples para operar o inversor.
    """
    OPTIONS = """
    ================== MENU ==================
    [1] Ligar motor
    [2] Parar motor
    [3] Definir velocidade (Hz)
    [4] Verificar temperatura (°C)
    [5] Verificar corrente (A)
    [6] Verificar tensão (V)
    [7] Definir sentido de giro (H=horário / A=anti-horário)
    [8] Verificar estado do motor
    [9] Iniciar com configuração padrão (30 Hz, horário)
    [0] Sair
    ==========================================
    """

    while True:
        print(OPTIONS)
        choice = input("Escolha uma opção: ").strip()

        try:
            if choice == "1":
                client.start_motor()
            elif choice == "2":
                client.stop_motor()
            elif choice == "3":
                val = float(input(f"Informe velocidade em Hz ({MIN_FREQ}–{MAX_FREQ}): ").strip())
                client.set_speed(val)
            elif choice == "4":
                client.read_temperature_c()
            elif choice == "5":
                client.read_current_a()
            elif choice == "6":
                client.read_voltage_v()
            elif choice == "7":
                m = input("Sentido (H/A): ").strip().upper()
                client.set_direction(forward=(m == "H"))
            elif choice == "8":
                st = client.read_status()
                print(f"Estado: {st['state']} | running={st['running']} | fault={st['fault']} | raw=0x{st['raw']:04X}")
            elif choice == "9":
                client.start_with_defaults()
            elif choice == "0":
                print("Saindo...")
                break
            else:
                print("Opção inválida.")
        except Exception as e:
            logger.error(f"Erro: {e}")

    client.conn.close()


def main():
    parser = argparse.ArgumentParser(description="Cliente Modbus TCP para inversor (SENAI).")
    parser.add_argument("--host", type=str, help="IP do inversor (ex.: 192.168.0.10)")
    parser.add_argument("--port", type=int, default=502, help="Porta Modbus TCP (default 502)")
    parser.add_argument("--slave", type=int, default=2, help="Slave ID (Unit ID)")
    parser.add_argument("--menu", action="store_true", help="Abrir menu interativo")
    args = parser.parse_args()

    # Se não foi passado host por argumento, pedir no terminal (para facilitar testes)
    #host = args.host or input("IP do inversor: ").strip()
    host = args.host or ("10.3.75.98").strip()
    port = args.port
    slave = args.slave

    regs = InverterRegisters()  # ajuste conforme manual
    client = InverterClient(host, port, slave, regs)

    # Se usar menu
    if args.menu or not args.host:
        interactive_menu(client)
    else:
        # Exemplo de uso direto (sem menu):
        client.start_with_defaults()
        status = client.read_status()
        print(f"Estado pós-inicialização: {status}")


if __name__ == "__main__":
    main()

