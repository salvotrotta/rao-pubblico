# -*- coding: utf-8 -*-
# Stdlib imports
import datetime
import hashlib
import logging

# Third-party app imports
import locale
import sys
import traceback

import jwt

# Core Django imports
from django.core import signing
from django.db.models import Q
from django.http import JsonResponse, HttpResponseRedirect
from django.urls import reverse

# Imports from your apps

import agency
from agency.classes.choices import RoleTag, StatusCode, RequestStatus
from agency.classes.tmp_mail_settings import TempMailSettings
from agency.classes.user_detail import UserDetail
from agency.models import Operator, IdentityRequest, SettingsRAO, Role, TokenUser, VerifyMail
from agency.utils import utils
from agency.utils.mail_utils import send_email
from django.conf import settings

from agency.utils.utils_api import create_api, reset_pin_api, disable_operator_api

LOG = logging.getLogger(__name__)


def populate_role():
    """
    Popola la tabella Role del db con le entry 'ADMIN' e 'OPERATOR' (se non presenti)
    :return: True/False
    """
    try:
        role = Role.objects.filter(role=RoleTag.ADMIN.value).last()
        if not role:
            r = Role(role=RoleTag.ADMIN.value, description="Accesso Administrator")
            r.save()
        role = Role.objects.filter(role=RoleTag.OPERATOR.value).last()
        if not role:
            r = Role(role=RoleTag.OPERATOR.value, description="Accesso Operatore")
            r.save()
        return True
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return False


def create_first_operator(request):
    """
    Genera un operatore Admin se non presente
    :param request: request
    :return: True/False
    """
    try:
        operator = Operator.objects.filter(idRole__role=RoleTag.ADMIN.value).last()
        if not operator:
            password = hashlib.sha256(request.session['passwordField'].encode()).hexdigest()

            hash_pass_insert = jwt.encode({'username': request.session['usernameField'],
                                           'exp': datetime.datetime.utcnow() + datetime.timedelta(days=30)}, password,
                                          algorithm='HS256')

            Operator.objects.create(name=request.session['nameField'],
                                    surname=request.session['surnameField'],
                                    fiscalNumber=request.session['usernameField'],
                                    email=request.session['emailField'],
                                    idRole=Role.objects.get(role=RoleTag.ADMIN.value),
                                    password=hash_pass_insert.decode("UTF-8"))

            return True
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))

    return False


def disable_operator(request, page, t):
    """
    Disabilita lo status dell'operatore selezionato
    :param request: request
    :param page: pagina della lista degli operatori da mostrare
    :param t: token
    :return: HttpResponseRedirect di list_operator
    """
    try:
        operator = get_operator_by_username(request.POST.get('username'))

        if operator and operator.idRole.role is not RoleTag.ADMIN.value:
            operator.status = False
            operator.save()
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))

    return HttpResponseRedirect(reverse('agency:list_operator', kwargs={'page': page, 't': t}))


def reset_pin_operator(request, page, t):
    """
    Reset del pin dell'operatore selezionato
    :param page: pagina della lista degli operatori da mostrare
    :param request: request
    :param t: token
    :return: HttpResponseRedirect di list_operator
    """
    try:
        operator = get_operator_by_username(request.POST.get('username_op'))

        if operator and operator.idRole.role is not RoleTag.ADMIN.value:

            pin = request.POST.get('pinField')

            status_code_reset, tmp_pin = reset_pin_api(pin, request.session.get('username'),
                                                       request.POST.get('username_op'))
            if status_code_reset == StatusCode.OK.value:
                status_code_disable = disable_operator_api(pin, request.session.get('username'),
                                                           request.POST.get('username_op'))
                if status_code_disable == StatusCode.OK.value:
                    request.session['pin'] = tmp_pin
                    operator.signStatus = False
                    operator.save()
                    params_t = signing.loads(t)
                    params_t['operator'] = request.POST.get('username_op')
                    t = signing.dumps(params_t)
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))

    return HttpResponseRedirect(reverse('agency:list_operator', kwargs={'page': page, 't': t}))


def get_all_operator():
    """
    Recupera dal db tutti gli operatori esistenti
    :return: Operator[]
    """
    return Operator.objects.all()


def get_operator_by_username(username):
    """
    Get operatore da Username
    :param username: username (codice fiscale) dell'operatore
    :return: Operator
    """
    return Operator.objects.filter(fiscalNumber=username).last()


def get_all_idr():
    """
    Restituisce la lista delle identity request presenti sul db
    :return: IdentityRequest[]
    """
    return IdentityRequest.objects.all().order_by('-timestamp_identification')


def get_idr_filter_operator(operator):
    """
    Restituisce la lista delle identity request presenti sul db, filtrate per operatore
    :param operator: idOperator da usare per filtrare la lista di IdentityRequest
    :return: IdentityRequest[]
    """
    return IdentityRequest.objects.all().order_by('-timestamp_identification').filter(idOperator=operator)


def send_recovery_link(username):
    """
    Invia una mail per il recupero password
    :param username: cf/username dell'operatore
    :return: StatusCode
    """
    try:
        operator = get_operator_by_username(username)
        if operator:
            if operator.status:
                params = {
                    'username': operator.fiscalNumber,
                    'name': operator.name,
                    'familyName': operator.surname,
                    'email': operator.email,
                }
                rao = get_attributes_RAO()
                t = signing.dumps(params)

                mail_elements = {
                    'nameUser': operator.name,
                    'familyNameUser': operator.surname,
                    'rao_name': rao.name
                }
                create_verify_mail_token(operator.email, t)

                send_email([operator.email], "Recupero password R.A.O.",
                           settings.TEMPLATE_URL_MAIL + 'mail_recovery_password.html',
                           {'activation_link': settings.BASE_URL + str(reverse('agency:redirect', kwargs={'t': t}))[1:],
                            'mail_elements': mail_elements})
                return StatusCode.OK.value
            else:
                return StatusCode.ERROR.value
        return StatusCode.NOT_FOUND.value
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return StatusCode.EXC.value


def update_status_operator(username, status=True):
    """
    Aggiorna lo stato dell'operatore
    :param username: username dell'operatore
    :param status: status da assegnare all'operatore (default: True)
    :return: StatusCode
    """
    try:
        operator = Operator.objects.filter(fiscalNumber=username).last()
        if operator:
            operator.status = status
            operator.failureCounter = 0
            operator.save()
            return StatusCode.OK.value
    except Exception as e:
        LOG.error("[{}] Si è verificato un errore durante l'update dello stato operatore: {}".format(username, str(e)))
        return StatusCode.EXC.value

    return StatusCode.ERROR.value


def get_status_operator(username):
    """
    Ritorna lo stato di un operatore
    :param username: username dell'operatore
    :return: status operatore
    """
    try:
        operator = Operator.objects.filter(fiscalNumber=username).last()
        if operator:
            return operator.status
    except Exception as e:
        LOG.error("[{}] Si è verificato un errore durante il recupero dello status: {}".format(username, str(e)))
        return False

    return False


def update_password_operator(username, new_password, status=True):
    """
    Aggiorna la password di un operatore
    :param username: cf/username dell'operatore
    :param new_password: nuova password dell'operatore
    :param status: status da assegnare all'operatore (default: True)
    :return: StatusCode
    """
    try:
        operator = Operator.objects.filter(fiscalNumber=username).last()
        if operator:
            check_operator = utils.check_operator(username, new_password, status)
            if check_operator != StatusCode.ERROR.value and check_operator != StatusCode.SIGN_NOT_AVAIBLE.value:
                return StatusCode.LAST_PWD.value
            password = hashlib.sha256(new_password.encode()).hexdigest()
            hash_pass_insert = jwt.encode(
                {'username': username, 'exp': datetime.datetime.utcnow() + datetime.timedelta(days=30)}, password,
                algorithm='HS256')
            operator.password = hash_pass_insert.decode("UTF-8")
            operator.status = True
            operator.failureCounter = 0
            operator.save()
            return StatusCode.OK.value
    except Exception as e:
        LOG.error("[{}] Si è verificato un errore durante l'update della password: {}".formt(username, str(e)))
        return StatusCode.EXC.value

    return StatusCode.ERROR.value


def create_operator(admin_username, operator):
    """
    Creazione di un operatore con ruolo Operator
    :param admin_username: username dell'admin
    :param operator: request.POST contenente i dati del nuovo operatore
    :return: StatusCode, pin nuovo operatore (in caso di StatusCode = 200)
    """
    name = agency.utils.utils.fix_name_surname(operator.get('name'))
    surname = agency.utils.utils.fix_name_surname(operator.get('familyName'))

    try:
        password = hashlib.sha256('password'.encode()).hexdigest()
        hash_pass_insert = jwt.encode({'username': operator.get('email'), 'exp': datetime.datetime.utcnow()}, password,
                                      algorithm='HS256')

        new_operator = Operator.objects.create(name=name,
                                               surname=surname,
                                               fiscalNumber=operator.get('fiscalNumber').upper(),
                                               email=operator.get('email'),
                                               idRole=Role.objects.get(role=RoleTag.OPERATOR.value),
                                               password=hash_pass_insert.decode("UTF-8"),
                                               status=False)

    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return StatusCode.ERROR.value, None

    try:
        status_code, op_temporary_pin = create_api(operator.get('pinField'), admin_username,
                                                  operator.get('fiscalNumber').upper())
        if status_code != StatusCode.OK.value:
            new_operator.delete()
            return StatusCode.EXC.value, None

        params = {
            'username': new_operator.fiscalNumber,
            'name': new_operator.name,
            'familyName': new_operator.surname,
            'email': new_operator.email,
            'pin': op_temporary_pin,
        }
        t = signing.dumps(params)

        rao = get_attributes_RAO()
        mail_elements = {
            'nameUser': new_operator.name,
            'familyNameUser': new_operator.surname,
            'rao_name': rao.name,
            'pin': op_temporary_pin,
            'is_admin': False
        }
        create_verify_mail_token(new_operator.email, t)

        mail_sended = send_email([new_operator.email], "Attivazione account R.A.O.",
                                 settings.TEMPLATE_URL_MAIL + 'mail_activation.html',
                                 {'activation_link': settings.BASE_URL + str(
                                     reverse('agency:redirect', kwargs={'t': t}))[1:],
                                  'mail_elements': mail_elements})
        if not mail_sended:
            return StatusCode.BAD_REQUEST.value, None
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return StatusCode.EXC.value, None
    return StatusCode.OK.value, op_temporary_pin


def create_identity(request, id_operator):
    """
    Creazione oggetto UserDetail per la richiesta identificazione.
    :param id_operator: id dell'operatore che crea la richiesta di identificazione
    :param request: request contenente i dati dell'identityRequest
    :return: oggetto UserDetail se l'identificazione è avvenuta con successo, None altrimenti
    """
    try:
        user = UserDetail(request.POST, id_operator)
        return user
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return None


def create_identity_request(request, identity):
    """
    Creazione entry IdentityRequest per la richiesta identificazione.
    :param request: request
    :param identity: oggetto UserDetail
    :return:
    """
    try:
        if 'username' in request.session:
            username_operator = request.session['username']
            token_user = create_token_user()
            if token_user:
                Operator.objects.get(fiscalNumber=username_operator, status=True)

                id_request = IdentityRequest(fiscalNumberUser=identity.get('fiscalNumber'),
                                             idOperator=Operator.objects.get(fiscalNumber=username_operator,
                                                                             status=True),
                                             status=RequestStatus.IDENTIFIED,
                                             timestamp_identification=datetime.datetime.utcnow(),
                                             token=token_user)

                id_request.save()

                return id_request
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
    return None


def delete_identity_request(identity):
    """
    Cancellazione richiesta di indentità
    :param identity: entry della tabella IdentityRequest da cancellare
    :return: StatusCode
    """
    if not identity:
        return StatusCode.NOT_FOUND.value

    try:
        token_user = identity.token
        identity.delete()
        token_user.delete()
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return StatusCode.EXC.value
    return StatusCode.OK.value


def create_token_user():
    """
    Creazione token associato all'IdentityRequest
    :return: token_user
    """
    token_user = None
    try:
        token_user = TokenUser(timestamp_creation=datetime.datetime.utcnow())
        token_user.save()
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
    return token_user


def get_attributes_RAO():
    """
    Restituisce gli attributi relativi al rao come il nome del Rao o l'issuerCode
    :return: SettingsRAO attributi relativi al rao o None in caso di errore
    """
    try:
        rao = SettingsRAO.objects.first()
        return rao
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return None


def search_filter(string, tab, operator=None):
    """
    Ricerca la stringa filter su tutte le colonne della tabella tab
    :param string: stringa da cercare
    :param tab: tabella su cui effettuare la ricerca (op=operator, id= identity_request)
    :param operator: operatore che effettua la ricerca (nel caso in cui non sia un admin a farlo)
    :return: entry filtrate di operators/identity_requests se l'operazione è eseguita con successo, None altrimenti
    """
    try:
        if tab == 'op':
            operators = Operator.objects.filter(
                Q(email__icontains=string) | Q(name__icontains=string) | Q(
                    surname__icontains=string) | Q(fiscalNumber__icontains=string))
            return operators
        elif tab == 'id':
            if operator is None:
                identity = IdentityRequest.objects.filter(
                    Q(fiscalNumberUser__icontains=string) | Q(timestamp_identification__icontains=string) |
                    Q(idOperator__name__icontains=string) | Q(idOperator__surname__icontains=string))
            else:
                identity = IdentityRequest.objects.filter(idOperator=operator)
                identity = identity.filter(
                    Q(fiscalNumberUser__icontains=string) | Q(timestamp_identification__icontains=string) |
                    Q(idOperator__name__icontains=string) | Q(idOperator__surname__icontains=string))
            return identity
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return None


def get_identification_report(days=4, week=0):
    """
    Report per pagina dashboard
    :param days: totale giorni + 1 restituiti
    :param week: 0 sta per settimana corrente, alterando questo valore è possibile spostarsi di settimana in settimana
    :return: lista di dizionari con numero identificazioni e il relativo giorno di quella settimana
    """
    try:
        locale.setlocale(locale.LC_ALL, "it_IT.UTF-8")
        datatime_now = datetime.datetime.utcnow()
        not_working_days = 0
        report = []
        i = days
        while i >= 0:
            days_for_filter = i + not_working_days
            datefilter = (datatime_now - datetime.timedelta(days=days_for_filter)) + datetime.timedelta(weeks=week)
            if i == days and datefilter.weekday() != 0:
                not_working_days -= 1
            else:
                if datefilter.weekday() == 5 or datefilter.weekday() == 6:
                    not_working_days -= 1
                else:
                    identified = IdentityRequest.objects.filter(timestamp_identification__day=datefilter.day,
                                                                timestamp_identification__month=datefilter.month,
                                                                timestamp_identification__year=datefilter.year).count()
                    report.append({'num_identified': identified, 'date': datefilter.strftime('%d-%b')})
                    i -= 1
        return report
    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
        return []


def get_weekly_identification_report(request):
    """
    Report per pagina dashboard ajax
    :param request: request
    :return: dizionario con stato richiesta, lista di numeri identificazioni e lista dei relativi giorno di quella settimana
    """
    try:
        week = request.GET.get("week")
        if not week:
            week = request.POST.get("week")

        if not week:
            return JsonResponse({'statusCode': StatusCode.ERROR.value})

        list_dict = get_identification_report(week=int(week))
        date = []
        num_identified = []
        for datedict in list_dict:
            date.append(datedict['date'])
            num_identified.append(datedict['num_identified'])
        return JsonResponse({'statusCode': StatusCode.OK.value, 'date': date, 'num_identified': num_identified})

    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))
    return JsonResponse({'statusCode': StatusCode.BAD_REQUEST.value})


def get_verify_mail_by_token(token):
    """
    Prelievo record delle mail di verifica
    :param token: token creato per la verifica della mail
    :return: tutte le entry di VerifyMail
    """
    return VerifyMail.objects.filter(token=token).last()


def create_verify_mail_token(email, token):
    """
    Crea un record nella tabella VerifyMail
    :param email: indirizzo a cui inviare la mail di verifica
    :param token: token creato per la verifica della mail
    """

    vm = VerifyMail(token=token,
                    creationDate=datetime.datetime.utcnow(),
                    expiredDate=datetime.datetime.utcnow() + datetime.timedelta(days=3),
                    email=email)
    vm.save()
    return


def set_is_verified(token):
    """
    Imposta il campo isVerified del link di verifica ricevuto via mail a True
    :param token: token creato per la verifica della mail
    """
    to_set = get_verify_mail_by_token(token=token)
    to_set.isVerified = True
    to_set.save()
    return


def check_db_not_altered():
    """
    Verifica che il DB non sia stato modificato dall'esterno per inserire un nuovo ADMIN
    L'ADMIN è solo uno.
    """
    op = Operator.objects.filter(idRole__role=RoleTag.ADMIN.value, status=True).count()
    if op > 1:
        return False
    else:
        return True


def update_sign_field_operator(username, status=True):
    """
    Imposta il campo signStatus dell'operatore a True
    :param username: username dell'operatore
    :param status: True/False da assegnare al signStatus
    """
    operator = get_operator_by_username(username)
    operator.signStatus = status
    operator.save()


def resend_mail_activation(request, page, t):
    """
    Invia nuovamente la mail di attivazione
    :param request:
    :param page: pagina della lista degli operatori da mostrare
    :param t: token
    :return:
    """
    try:
        operator = get_operator_by_username(request.POST.get('username_op'))

        if operator and operator.idRole.role is not RoleTag.ADMIN.value:

            pin = request.POST.get('pinField')

            status_code_reset, tmp_pin = reset_pin_api(pin, request.session.get('username'),
                                                       request.POST.get('username_op'))
            if status_code_reset == StatusCode.OK.value:
                status_code_disable = disable_operator_api(pin, request.session.get('username'),
                                                           request.POST.get('username_op'))
                if status_code_disable == StatusCode.OK.value:
                    request.session['pin'] = tmp_pin
                    operator.signStatus = False
                    operator.save()
                    params_t = signing.loads(t)
                    params_t['operator'] = request.POST.get('username_op')
                    t = signing.dumps(params_t)
                    params = {
                        'username': operator.fiscalNumber,
                        'name': operator.name,
                        'familyName': operator.surname,
                        'email': operator.email,
                        'pin': tmp_pin,
                    }

                    t_mail = signing.dumps(params)

                    rao = get_attributes_RAO()
                    mail_elements = {
                        'nameUser': operator.name,
                        'familyNameUser': operator.surname,
                        'rao_name': rao.name,
                        'pin': tmp_pin,
                        'is_admin': False
                    }
                    create_verify_mail_token(operator.email, t_mail)

                    send_email([operator.email], "Attivazione account R.A.O.",
                               settings.TEMPLATE_URL_MAIL + 'mail_activation.html',
                               {'activation_link': settings.BASE_URL + str(
                                   reverse('agency:redirect', kwargs={'t': t_mail}))[1:],
                                'mail_elements': mail_elements})

    except Exception as e:
        LOG.error("Exception: {}".format(str(e)))

    return HttpResponseRedirect(reverse('agency:list_operator', kwargs={'page': page, 't': t}))


def update_emailrao(op, rao_name, rao_email, rao_host, rao_pwd, email_crypto_type, email_port, smtp_mail_from=""):
    """
    Aggiorna la tabella impostazioni del RAO
    :param op: operatore (admin) che effettua l'operazione di aggiornamento
    :param rao_name: nome del Rao
    :param rao_email: nome di chi invia l'email
    :param rao_host: host dell'email
    :param rao_pwd: password dell'email
    :param email_crypto_type: tipo di Crittografia (Nessuna/TLS/SSL)
    :param email_port: porta in uscita
    :param smtp_mail_from: server mail SMTP
    :return: True/False
    """
    try:

        entry_rao = SettingsRAO.objects.first()
        if not entry_rao:
            return False
        else:
            password = agency.utils.utils.encrypt_data(rao_pwd, settings.SECRET_KEY_ENC)

            tmp_settings = TempMailSettings(smtp_mail_from, rao_email, rao_host, password=password, email_port=email_port,
                                            email_crypto_type=email_crypto_type)
            mail_elements = {
                'nameUser': op.name,
                'familyNameUser': op.surname,
                'rao_name': rao_name
            }
            mail_sent = send_email([op.email], "Email di prova",
                                   settings.TEMPLATE_URL_MAIL + 'verify_mail_address.html',
                                   {'mail_elements': mail_elements}, conn_settings=tmp_settings)
            if mail_sent == StatusCode.OK.value:
                entry_rao.email = smtp_mail_from
                entry_rao.username = rao_email
                entry_rao.host = rao_host
                entry_rao.password = password
                entry_rao.port = email_port
                entry_rao.crypto = email_crypto_type
                entry_rao.save()
                LOG.debug("Messaggio inviato correttamente")
                return True
            else:
                LOG.debug("Messaggio non inviato")
                return False
    except Exception as e:
        ype, value, tb = sys.exc_info()
        LOG.error("Exception: {}".format(str(e)))
        LOG.error('exception_value = %s, value = %s' % (value, type,))
        LOG.error('tb = %s' % traceback.format_exception(type, value, tb))
    return False