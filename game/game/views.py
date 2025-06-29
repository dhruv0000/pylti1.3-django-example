import datetime
import os
import pprint

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.urls import reverse
from pylti1p3.contrib.django import DjangoOIDCLogin, DjangoMessageLaunch, DjangoCacheDataStorage
from pylti1p3.deep_link_resource import DeepLinkResource
from pylti1p3.grade import Grade
from pylti1p3.lineitem import LineItem
from pylti1p3.tool_config import ToolConfJsonFile
from pylti1p3.registration import Registration


PAGE_TITLE = 'Game Example'


class ExtendedDjangoMessageLaunch(DjangoMessageLaunch):

    def validate_nonce(self):
        """
        Probably it is bug on "https://lti-ri.imsglobal.org":
        site passes invalid "nonce" value during deep links launch.
        Because of this in case of iss == http://imsglobal.org just skip nonce validation.

        """
        iss = self.get_iss()
        deep_link_launch = self.is_deep_link_launch()
        if iss == "http://imsglobal.org" and deep_link_launch:
            return self
        return super().validate_nonce()


def get_lti_config_path():
    return os.path.join(settings.BASE_DIR, '..', 'configs', 'game.json')


def get_tool_conf():
    tool_conf = ToolConfJsonFile(get_lti_config_path())
    return tool_conf


def get_jwk_from_public_key(key_name):
    key_path = os.path.join(settings.BASE_DIR, '..', 'configs', key_name)
    f = open(key_path, 'r')
    key_content = f.read()
    jwk = Registration.get_jwk(key_content)
    f.close()
    return jwk


def get_launch_data_storage():
    return DjangoCacheDataStorage()


def get_launch_url(request):
    target_link_uri = request.POST.get('target_link_uri', request.GET.get('target_link_uri'))
    if not target_link_uri:
        raise Exception('Missing "target_link_uri" param')
    return target_link_uri


def login(request):
    tool_conf = get_tool_conf()
    launch_data_storage = get_launch_data_storage()

    pprint.pprint('Tool Config:')
    # pprint.pprint(tool_conf.get_json())

    oidc_login = DjangoOIDCLogin(request, tool_conf, launch_data_storage=launch_data_storage)
    target_link_uri = get_launch_url(request)
    pprint.pprint('Target Link:')
    # pprint.pprint(oidc_login.get_json())
    return oidc_login\
        .enable_check_cookies()\
        .redirect(target_link_uri)


@require_POST
def launch(request):
    tool_conf = get_tool_conf()
    launch_data_storage = get_launch_data_storage()
    message_launch = ExtendedDjangoMessageLaunch(request, tool_conf, launch_data_storage=launch_data_storage)
    message_launch_data = message_launch.get_launch_data()
    pprint.pprint('Launch data:')
    pprint.pprint(message_launch_data)

    difficulty = message_launch_data.get('https://purl.imsglobal.org/spec/lti/claim/custom', {})\
        .get('difficulty', None)
    

    if not difficulty:
        difficulty = request.GET.get('difficulty', 'normal')

    return render(request, 'game.html', {
        'page_title': PAGE_TITLE,
        'is_deep_link_launch': message_launch.is_deep_link_launch(),
        'launch_data': message_launch.get_launch_data(),
        'launch_id': message_launch.get_launch_id(),
        'curr_user_name': message_launch_data.get('name', ''),
        'curr_diff': difficulty
    })


def get_jwks(request):
    tool_conf = get_tool_conf()
    return JsonResponse(tool_conf.get_jwks(), safe=False)


def configure(request, launch_id, difficulty):
    tool_conf = get_tool_conf()
    launch_data_storage = get_launch_data_storage()
    message_launch = ExtendedDjangoMessageLaunch.from_cache(launch_id, request, tool_conf,
                                                            launch_data_storage=launch_data_storage)

    if not message_launch.is_deep_link_launch():
        return HttpResponseForbidden('Must be a deep link!')

    launch_url = request.build_absolute_uri(reverse('game-launch') + '?difficulty=' + difficulty)

    resource = DeepLinkResource()
    resource.set_url(launch_url)\
        .set_custom_params({'difficulty': difficulty})\
        .set_title('Breakout ' + difficulty + ' mode!')

    html = message_launch.get_deep_link().output_response_form([resource])
    return HttpResponse(html)


@require_POST
def score(request, launch_id, earned_score, time_spent):
    tool_conf = get_tool_conf()
    launch_data_storage = get_launch_data_storage()
    message_launch = ExtendedDjangoMessageLaunch.from_cache(launch_id, request, tool_conf,
                                                            launch_data_storage=launch_data_storage)
    resource_link_id = message_launch.get_launch_data() \
        .get('https://purl.imsglobal.org/spec/lti/claim/resource_link', {}).get('id')

    if not message_launch.has_ags():
        return HttpResponseForbidden("Don't have grades!")

    sub = message_launch.get_launch_data().get('sub')
    timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
    earned_score = int(earned_score)
    time_spent = int(time_spent)

    ags = message_launch.get_ags()

    if ags.can_create_lineitem():
        sc = Grade()
        sc.set_score_given(earned_score)\
            .set_score_maximum(100)\
            .set_timestamp(timestamp)\
            .set_activity_progress('Completed')\
            .set_grading_progress('FullyGraded')\
            .set_user_id(sub)

        sc_line_item = LineItem()
        sc_line_item.set_tag('score')\
            .set_score_maximum(100)\
            .set_label('Score')
        if resource_link_id:
            sc_line_item.set_resource_id(resource_link_id)

        ags.put_grade(sc, sc_line_item)

        tm = Grade()
        tm.set_score_given(time_spent)\
            .set_score_maximum(999)\
            .set_timestamp(timestamp)\
            .set_activity_progress('Completed')\
            .set_grading_progress('FullyGraded')\
            .set_user_id(sub)

        tm_line_item = LineItem()
        tm_line_item.set_tag('time')\
            .set_score_maximum(999)\
            .set_label('Time Taken')
        if resource_link_id:
            tm_line_item.set_resource_id(resource_link_id)

        result = ags.put_grade(tm, tm_line_item)
    else:
        sc = Grade()
        sc.set_score_given(earned_score) \
            .set_score_maximum(100) \
            .set_timestamp(timestamp) \
            .set_activity_progress('Completed') \
            .set_grading_progress('FullyGraded') \
            .set_user_id(sub)
        result = ags.put_grade(sc)

    return JsonResponse({'success': True, 'result': result.get('body')})


def scoreboard(request, launch_id):
    tool_conf = get_tool_conf()
    launch_data_storage = get_launch_data_storage()
    message_launch = ExtendedDjangoMessageLaunch.from_cache(launch_id, request, tool_conf,
                                                            launch_data_storage=launch_data_storage)
    pprint.pprint(message_launch.get_launch_data())
    
    resource_link_id = message_launch.get_launch_data() \
        .get('https://purl.imsglobal.org/spec/lti/claim/resource_link', {}).get('id')

    if not message_launch.has_nrps():
        return HttpResponseForbidden("Don't have names and roles!")

    if not message_launch.has_ags():
        return HttpResponseForbidden("Don't have grades!")

    ags = message_launch.get_ags()

    if ags.can_create_lineitem():
        score_line_item = LineItem()
        score_line_item.set_tag('score') \
            .set_score_maximum(100) \
            .set_label('Score')
        if resource_link_id:
            score_line_item.set_resource_id(resource_link_id)

        score_line_item = ags.find_or_create_lineitem(score_line_item)
        scores = ags.get_grades(score_line_item)

        time_line_item = LineItem()
        time_line_item.set_tag('time') \
            .set_score_maximum(999) \
            .set_label('Time Taken')
        if resource_link_id:
            time_line_item.set_resource_id(resource_link_id)

        time_line_item = ags.find_or_create_lineitem(time_line_item)
        times = ags.get_grades(time_line_item)
    else:
        scores = ags.get_grades()
        times = None

    members = message_launch.get_nrps().get_members()
    scoreboard_result = []

    for sc in scores:
        result = {'score': sc['resultScore']}
        if times is None:
            result['time'] = 'Not set'
        else:
            for tm in times:
                if tm['userId'] == sc['userId']:
                    result['time'] = tm['resultScore']
                    break
        for member in members:
            if member['user_id'] == sc['userId']:
                result['name'] = member.get('name', 'Unknown')
                break
        scoreboard_result.append(result)

    return JsonResponse(scoreboard_result, safe=False)
