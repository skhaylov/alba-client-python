# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging
import requests
import hashlib
import json

from .card_token import CardTokenResponse
from .connection import ConnectionProfile
from .exceptions import CODE2EXCEPTION, MissArgumentError, AlbaException
from .sign import sign


class AlbaService(object):
    BASE_URL = 'https://partner.rficb.ru/'

    def __init__(self, service_id, secret, logger=None):
        """
        service_id идентификатор сервиса
        secret секретный ключ сервиса
        """
        self.service_id = service_id
        self.secret = secret
        self.connection_profile = ConnectionProfile.second()
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger(__name__)

    def _request(self, url, method, data):
        try:
            self.logger.debug('Sent {} request with params {}'
                              .format(method.upper(), data))

            if method == 'get':
                response = requests.get(url, params=data)
            else:
                response = requests.post(url, data)

            if response.status_code != 200:
                self.logger.debug(u'Server unavailable: {}'
                                  .format(response.status_code))
                raise AlbaException('Сервер не доступен: {}'
                                    .format(response.status_code))
        except requests.ConnectionError as e:
            raise AlbaException(e)

        content = response.content.decode('utf-8')
        self.logger.debug('Server response: {}'.format(content))

        json_response = json.loads(content)
        if json_response['status'] == 'error':
            msg = json_response.get('msg', json_response.get('message'))
            code = json_response.get('code', 'unknown')
            raise CODE2EXCEPTION.get(code, AlbaException)(msg)

        return json_response

    def _get(self, url, data=None):
        return self._request(url, 'get', data)

    def _post(self, url, data=None):
        return self._request(url, 'post', data)

    def pay_types(self):
        """
        Получение списка доступных способов оплаты для сервиса
        """
        check = hashlib.md5((str(self.service_id) + self.secret).encode('utf-8'))
        check = check.hexdigest()
        url = ("%salba/pay_types/?service_id=%s&check=%s" %
               (self.BASE_URL, self.service_id, check))
        return self._get(url)['types']

    def init_payment(self, pay_type, cost, name, email, phone,
                     order_id=None, comment=None, bank_params=None,
                     commission=None, card_token=None, recurrent_params=None,
                     **kwargs):
        """
        Инициация оплаты
        pay_type способ оплаты
        cost сумма платежа
        name наименование товара
        email e-mail покупателя
        order_id идентификатор заказа
        comment комментарий заказа
        bank_params параметры для перевода на реквизиты
        commission на ком лежит комиссия на абоненте или партнере
          допустимые значение: 'partner', 'abonent'
        """
        fields = {
            "cost": cost,
            "name": name,
            "email": email,
            "phone_number": phone,
            "background": "1",
            "type": pay_type,
            "service_id": self.service_id,
            "version": "2.0"
        }
        if order_id:
            fields['order_id'] = order_id
        if comment:
            fields['comment'] = comment
        if bank_params:
            for bkey, bval in bank_params.items():
                fields[bkey] = bval
        if commission:
            fields['commission'] = commission
        if card_token:
            fields['card_token'] = card_token
        if recurrent_params:
            fields['recurrent_params'] = recurrent_params

        fields.update(kwargs)

        url = self.BASE_URL + "alba/input/"
        fields['check'] = sign("POST", url, fields, self.secret)

        return self._post(url, fields)

    def transaction_details(self, tid=None, order_id=None):
        """
        Получение информации о транзакции
        tid идентификатор транзакции
        """
        if tid:
            params = {'tid': tid}
        elif order_id:
            params = {'order_id': order_id, 'service_id': self.service_id}
        else:
            raise MissArgumentError('Ожидается аргумент tid или order_id')

        url = self.BASE_URL + "alba/details/"
        params['version'] = '2.0'
        params['check'] = sign("POST", url, params, self.secret)
        answer = self._post(url, params)
        return answer

    def refund(self, tid, amount=None, test=False, reason=None):
        """
        проведение возврата
        """
        url = self.BASE_URL + "alba/refund/"
        fields = {'version': '2.0',
                  'tid': tid}
        if amount:
            fields['amount'] = amount

        if test:
            fields['test'] = '1'

        if reason:
            fields['reason'] = reason

        fields['check'] = sign("POST", url, fields, self.secret)
        answer = self._post(url, fields)
        return answer

    def gate_details(self, gate):
        """
        получение информации о шлюзе
        gate короткое имя шлюза
        """
        url = self.BASE_URL + "alba/gate_details/"
        params = {'version': '2.0',
                  'gate': gate,
                  'service_id': self.service_id}
        params['check'] = sign("GET", url, params, self.secret)
        answer = self._get(url, params)
        return answer

    def check_callback_sign(self, post):
        """
        Обработка нотификации
        array $post Массив $_POST параметров
        """
        order = ['tid',
                 'name',
                 'comment',
                 'partner_id',
                 'service_id',
                 'order_id',
                 'type',
                 'cost',
                 'income_total',
                 'income',
                 'partner_income',
                 'system_income',
                 'command',
                 'phone_number',
                 'email',
                 'resultStr',
                 'date_created',
                 'version']
        params = [post.get(field, '') for field in order]
        params.append(self.secret)
        return hashlib.md5(
            (''.join(params)).encode('utf-8')).hexdigest() == post['check']

    def create_card_token(self, request, test):
        month = request.exp_month
        if len(month) == 1:
            month = '0' + month

        params = {
            'service_id': request.service_id,
            'card': request.card,
            'exp_month': month,
            'exp_year': request.exp_year,
            'cvc': request.cvc
        }
        if request.card_holder:
            params.update({'card_holder': request.card_holder})

        url = (self.connection_profile.card_token_test_url if test
               else self.connection_profile.card_token_url)
        result = self._post(url + 'create', params)
        response = CardTokenResponse(result)
        return response

    def cancel_recurrent_payment(self, order_id):
        url = self.BASE_URL + 'alba/recurrent_change/'
        fields = {
            'operation': 'cancel',
            'order_id': order_id,
            'service_id': self.service_id,
            'version': '2.0'
        }
        fields['check'] = sign('POST', url, fields, self.secret)
        return self._post(url, fields)
