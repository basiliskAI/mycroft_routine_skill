from adapt.intent import IntentBuilder
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from collections import defaultdict
from mycroft.skills.core import MycroftSkill, intent_handler
from mycroft.util.log import getLogger
from os.path import join, dirname, abspath
from websocket import create_connection
import json
import re


__author__ = 'ChristopherRogers1991'

LOGGER = getLogger(__name__)
URL_TEMPLATE = "{scheme}://{host}:{port}{path}"
ROUTINES_FILENAME = "routines.json"

def send_message(message, host="localhost",
                 port=8181, path="/core", scheme="ws"):
    payload = json.dumps({
        "type": "recognizer_loop:utterance",
        "context": "",
        "data": {
            "utterances": [message]
        }
    })
    url = URL_TEMPLATE.format(scheme=scheme, host=host,
                              port=str(port), path=path)
    ws = create_connection(url)
    ws.send(payload)
    ws.close()


class MycroftRoutineSkill(MycroftSkill):

    def __init__(self):
        super(MycroftRoutineSkill, self).__init__(name="MycroftRoutineSkill")

    def initialize(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()

        self._routines = defaultdict(dict)
        self._routines.update(self._load_routine_data())

        self._routine_to_sched_id_map = {}
        self._register_routines()

        path = dirname(abspath(__file__))

        path_to_stop_words = join(path, 'vocab', self.lang, 'ThatsAll.voc')
        self._stop_words = self._lines_from_path(path_to_stop_words)

        path_to_cancel_words = join(path, 'vocab', self.lang, 'Cancel.voc')
        self._cancel_words = self._lines_from_path(path_to_cancel_words)

        path_to_days_of_week = join(path, 'vocab', self.lang, 'DaysOfWeek.voc')
        self._days_of_week = self._lines_from_path(path_to_days_of_week)



    def _lines_from_path(self, path):
        with open(path, 'r') as file:
            lines = [line.strip().lower() for line in file]
            return lines

    def _load_routine_data(self):
        try:
            with self.file_system.open(ROUTINES_FILENAME, 'r') as conf_file:
                return json.loads(conf_file.read())
        except FileNotFoundError:
            log_message = "Routines file not found."
        except PermissionError:
            log_message = "Permission denied when reading routines file."
        except json.decoder.JSONDecodeError:
            log_message = "Error decoding json from routines file."
        log_message += " Initializing empty dictionary."
        return {}

    def _register_routines(self):
        for routine in self._routines:
            self._register_routine(routine)

    def _register_routine(self, name):
        self.register_vocabulary(name, "RoutineName")
        schedule = self._routines[name].get('schedule')
        if schedule and self._routines.get('enabled', True):
            self._schedule_routine(name, schedule)

    def _schedule_routine(self, name, cronstring):
        trigger = CronTrigger.from_crontab(cronstring)
        job = self.scheduler.add_job(func=self._run_routine,
                               args=[name], trigger=trigger, name=name)
        self._routine_to_sched_id_map[name] = job.id

    def _write_routine_data(self):
        with self.file_system.open(ROUTINES_FILENAME, 'w') as conf_file:
            conf_file.write(json.dumps(self._routines, indent=4))

    @intent_handler(IntentBuilder("CreateRoutine").require("Create").require("Routine"))
    def _create_routine(self, message):
        name = self.get_response("name.it")
        if not name:
            return
        name = name.lower()
        if name in self._cancel_words:
            return

        tasks = self._get_task_list()
        if not tasks:
            return

        self._routines[name]['tasks'] = tasks

        self._write_routine_data()
        self._register_routine(name)
        self.speak_dialog('created', data={"name": name})

    def _get_task_list(self):
        first_task = self.get_response("first.task")
        if not first_task:
            return []
        first_task = first_task.lower()
        if first_task in self._cancel_words:
            return []
        tasks = [first_task]
        while True:
            task = self.get_response("next")
            if not task:
                return []
            task = task.lower()
            if task in self._cancel_words:
                return []
            if task in self._stop_words:
                break
            tasks.append(task)
        return tasks

    @intent_handler(IntentBuilder("RunRoutine").optionally("Run").require("RoutineName"))
    def _trigger_routine(self, message):
        name = message.data["RoutineName"]
        self._run_routine(name)

    def _run_routine(self, name):
        for task in self._routines[name]['tasks']:
            send_message(task)

    @intent_handler(IntentBuilder("ListRoutine").require("List").require("Routines"))
    def _list_routines(self, message):
        if not self._routines:
            self.speak_dialog('no.routines')
            return
        routines = ". ".join(self._routines.keys())
        self.speak_dialog('list.routines')
        self.speak(routines)

    @intent_handler(IntentBuilder("DeleteRoutine").require("Delete").require("RoutineName"))
    def _delete_routine(self, message):
        name = message.data["RoutineName"]
        del(self._routines[name])
        self._write_routine_data()
        self.speak_dialog('deleted', data={"name": name})

    @intent_handler(IntentBuilder("DescribeRoutine").require("Describe").require("RoutineName"))
    def _describe_routine(self, message):
        name = message.data["RoutineName"]
        tasks = ". ".join(self._routines[name]['tasks'])
        self.speak_dialog('describe', data={"name": name})
        self.speak(tasks)

    @intent_handler(IntentBuilder("ScheduleRoutine").require("Schedule").require("RoutineName"))
    def _add_routine_schedule(self, message):
        name = message.data["RoutineName"]
        days = self._get_days()
        hour, minute = self._get_time()
        cronstring = self._generate_cronstring(days, hour, minute)
        self._routines[name]['schedule'] = cronstring
        self._routines[name]['enabled'] = True
        self._write_routine_data()
        self._schedule_routine(name, cronstring)
        self.speak_dialog("scheduled", data={'name': name})

    @intent_handler(IntentBuilder("DisableRoutine").require("Disable").require("RoutineName"))
    def _disable_scheduled_routine(self, message):
        name = message.data["RoutineName"]
        self._routines[name]['enabled'] = False
        self._write_routine_data()
        self.scheduler.remove_job(self._routine_to_sched_id_map[name])
        self.speak_dialog("disabled", data={"name": name})

    @intent_handler(IntentBuilder("EnableRoutine").require("Enable").require("RoutineName"))
    def _enable_scheduled_routine(self, message):
        name = message.data["RoutineName"]
        self._routines[name]['enabled'] = True
        self._write_routine_data()
        self._schedule_routine(name, self._routines[name]["schedule"])
        self.speak_dialog("enabled", data={"name": name})

    def _get_days(self):
        days_to_run = []
        days_from_user = self.get_response('which.days')
        if not days_from_user:
            return
        days_from_user = days_from_user.lower()
        for i in range(len(self._days_of_week)):
            if self._days_of_week[i] in days_from_user:
                days_to_run.append(str(i))
        return ','.join(days_to_run)

    def _get_time(self):
        regex = '(?P<hour>[0-9]{1,2})[: ](?P<minute>[0-9]{1,2}) (?P<time_of_day>[ap].?m.?)'
        time_from_user = self.get_response('what.time')
        if not time_from_user:
            return
        time_from_user = time_from_user.lower()
        matches = re.match(regex, time_from_user)

        if not matches:
            self.speak_dialog('could.not.parse.time')
            return

        matches = matches.groupdict()
        hour = int(matches['hour'])
        minute = int(matches['minute'])
        pm = matches['time_of_day'] == 'pm'

        hour = hour % 12
        hour += 12 if pm else 0

        return hour, minute

    def _generate_cronstring(self, days, hour, minute):
        return '{m} {h} * * {d}'.format(m=minute, h=hour, d=days)

def create_skill():
    return MycroftRoutineSkill()
