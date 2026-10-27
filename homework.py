from http import HTTPStatus
from logging.handlers import RotatingFileHandler
import logging
import os
import time
import sys

from dotenv import load_dotenv
import requests
from telebot import TeleBot

load_dotenv()

PRACTICUM_TOKEN = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

RETRY_PERIOD = 600
ENDPOINT = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}

HOMEWORK_VERDICTS = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}

REQUIRED_ENV_VARS = [
    'PRACTICUM_TOKEN',
    'TELEGRAM_TOKEN',
    'TELEGRAM_CHAT_ID'
]

STATUS_CHANGED_TEMPLATE = ('Изменился статус проверки работы '
                           '"{homework_name}". {status_message}')

MISSING_ENV_MESSAGE = (
    'Отсутствуют обязательные переменные окружения: {missing_vars}. '
    'Программа не может продолжить работу.'
)

SEND_MESSAGE_ERROR = (
    'Ошибка отправки сообщения: "{message}". Ошибка: {error}'
)

SEND_MESSAGE_SUCCESS = 'Успешно отправлено сообщение - "{message}"'

TYPE_ERROR_EXPECTED_DICT = (
    'Ожидался словарь (dict), но получен {actual_type}'
)

KEY_ERROR_MISSING_HOMEWORKS = (
    'Отсутствует ожидаемый ключ "homeworks" в ответе API'
)

TYPE_ERROR_EXPECTED_LIST = (
    'Под ключом "homeworks" ожидается список (list), '
    'но получен {actual_type}'
)

CONNECTION_ERROR_API = (
    "Не удалось подключиться к API: {endpoint}. "
    "Параметры: {params} {headers}. Ошибка: {error}"
)

API_ERROR_STATUS_CODE = (
    "Получен неверный код ответа API: {status_code}. "
    "Эндпоинт: {endpoint}. Параметры: {params}  {headers}"
)

API_ERROR_CODE = (
    'Ошибка в ответе API: получен код ошибки {error_code}. '
    "При {endpoint} params={params} headers={headers}"
)

KEY_ERROR_MISSING_NAME = 'В полученных данных отсутствует поле "homework_name"'

KEY_ERROR_MISSING_STATUS = ('В полученных данных отсутствует поле '
                            'статуса (status)')

VALUE_ERROR_UNKNOWN_STATUS = 'Неизвестный статус домашней работы: {status}'

MISSING_NEW_STATUS = 'В ответе отсутствует новый статус'

ERORR_TEMPLATE = 'Произошла ошибка: {error}'


def check_tokens():
    """Проверяет доступность переменных окружения.
    Функция проверяет, что все переменные окружения, требуемые для работы бота,
    заданы.
    """
    missing = [
        name
        for name in REQUIRED_ENV_VARS
        if globals().get(name) is None
    ]
    if missing:
        message = MISSING_ENV_MESSAGE.format(missing_vars=missing)
        logging.critical(message)
    return not missing


def send_message(bot, message):
    """Отправляет сообщение в Telegram чат.
    Чат определяется переменной окружения `TELEGRAM_CHAT_ID`.
    """
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as error:
        logging.error(SEND_MESSAGE_ERROR.format(
            message=message,
            error=error
        ))
        return False
    logging.debug(SEND_MESSAGE_SUCCESS.format(message=message))
    return True


def check_response(response):
    """Проверяет ответ API на соответствие документации.
    Проверяет, что ответ API соответствует ожидаемой структуре
    из документации урока «API сервиса Практикум Домашка».
    """
    if not isinstance(response, dict):
        raise TypeError(TYPE_ERROR_EXPECTED_DICT.format(
            actual_type=type(response).__name__
        ))
    if 'homeworks' not in response:
        raise KeyError(KEY_ERROR_MISSING_HOMEWORKS)
    homework = response['homeworks']
    if not isinstance(homework, list):
        raise TypeError(TYPE_ERROR_EXPECTED_LIST.format(
            actual_type=type(homework).__name__
        ))


def get_api_answer(timestamp):
    """Делает запрос к эндпоинту API сервиса.
    В качестве параметра передаётся временная метка. При успешном запросе
    возвращает ответ API, преобразованный из JSON в типы данных Python.
    """
    payload = {'from_date': timestamp}
    try:
        response = requests.get(ENDPOINT, headers=HEADERS, params=payload)
    except requests.RequestException as error:
        raise ConnectionError(CONNECTION_ERROR_API.format(
            endpoint=ENDPOINT,
            headers=HEADERS,
            params=payload,
            error=error
        ))
    status_code = response.status_code
    if status_code != HTTPStatus.OK:
        raise RuntimeError(API_ERROR_STATUS_CODE.format(
            endpoint=ENDPOINT,
            params=payload,
            headers=HEADERS,
            status_code=status_code
        ))
    response_data = response.json()
    words_denial = {'code', 'error'}
    for word in words_denial:
        if word in response_data:
            raise RuntimeError(API_ERROR_CODE.format(
                error_code=(word, response_data[word]),
                endpoint=ENDPOINT,
                headers=HEADERS,
                params=payload
            ))
    return response_data


def parse_status(homework):
    """Извлекает статус домашней работы из данных.
    Получает один элемент из списка домашних работ и возвращает строку
    для отправки в Telegram с вердиктом из словаря `HOMEWORK_VERDICTS`.
    """
    if 'homework_name' not in homework:
        raise KeyError(KEY_ERROR_MISSING_NAME)
    homework_name = homework['homework_name']
    if 'status' not in homework:
        raise KeyError(KEY_ERROR_MISSING_STATUS)
    status = homework['status']
    if status not in HOMEWORK_VERDICTS:
        raise ValueError(VALUE_ERROR_UNKNOWN_STATUS.format(status=status))
    return STATUS_CHANGED_TEMPLATE.format(
        homework_name=homework_name,
        status_message=HOMEWORK_VERDICTS[status]
    )


def main():
    """Основная логика работы бота."""
    if not check_tokens():
        return
    bot = TeleBot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    last_error = None
    while True:
        try:
            response = get_api_answer(timestamp)
            check_response(response)
            homeworks = response['homeworks']
            if homeworks:
                homework = homeworks[0]
                message = parse_status(homework)
                if send_message(bot=bot, message=message):
                    timestamp = response.get('current_date', timestamp)
                    last_error = None
            else:
                logging.debug(MISSING_NEW_STATUS)
        except Exception as error:
            error_message = ERORR_TEMPLATE.format(error=error)
            logging.exception(error_message)
            if (error_message != last_error
                    and send_message(bot=bot, message=error_message)):
                last_error = error_message
        time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            RotatingFileHandler(
                f'{__file__}.log',
                maxBytes=50000000,
                backupCount=5
            ),
            logging.StreamHandler(sys.stdout)
        ]
    )
    main()
