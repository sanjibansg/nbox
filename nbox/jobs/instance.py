"""
Jobs
====

Run arbitrary scripts on NimbleBox.ai instances with simplicity of ``nbox``.

This immensely powerful tool will allow methods to be run in a truly serverless fashion.
In the background it will spin up a VM, then run a script and close the machine. This is
the second step towards YoCo and CasH, read more
`here <https://yashbonde.github.io/general-perceivers/remote.html>`_.
"""

import os
import re
import sys
import time
from functools import partial
from tabulate import tabulate
from requests.sessions import Session

from .utils import SpecSubway, Subway, TIMEOUT_CALLS
from ..utils import NBOX_HOME_DIR, join, logger
from ..init import nbox_session
from ..auth import secret


################################################################################
# NBX-Instances Functions
# =======================
# The methods below are used to talk to the Webserver APIs and other methods
# make the entire process functional.
################################################################################

def print_status(session = nbox_session):
  url = secret.get("nbx_url")
  r = session.get(f"{url}/api/instance/get_user_instances")
  r.raise_for_status()
  message = r.json()["msg"]
  if message != "success":
    raise Exception(message)

  money = r.json()["nbBucks"]
  data = [{k: x[k] for k in Instance.useful_keys} for x in r.json()["data"]]

  logger.info(f"Total NimbleBox.ai credits left: {money}")
  data_table = [[x[k] for k in Instance.useful_keys] for x in data]
  for x in tabulate(data_table, headers=Instance.useful_keys).splitlines():
    logger.info(x)

################################################################################
# NimbleBox.ai Instances
# ======================
# NBX-Instances is compute abstracted away to get the best hardware for your
# task. To that end each Instance from the platform is a single class.
################################################################################

class Instance():
  # each instance has a lot of data against it, we need to store only a few as attributes
  useful_keys = ["state", "used_size", "total_size", "public", "instance_id", "name"]

  def __init__(self, i, cs_endpoint = "server"):
    super().__init__()

    self.url = secret.get('nbx_url')
    self.cs_url = None
    self.cs_endpoint = cs_endpoint
    self.session = Session()
    self.session.headers.update({"Authorization": f"Bearer {secret.get('access_token')}"})
    self.web_server = Subway(f"{self.url}/api/instance", self.session)
    logger.debug(f"WS: {self.web_server}")

    self.instance_id = None
    self.__opened = False
    self.running_scripts = []

    self.refresh(i)
    logger.debug(f"Instance added: {self.name} ({self.instance_id})")

    if self.state == "RUNNING":
      self.start()

  __repr__ = lambda self: f"<Instance ({', '.join([f'{k}:{getattr(self, k)}' for k in self.useful_keys + ['cs_url']])})>"

  print_status = staticmethod(print_status)

  @classmethod
  def new(cls, name) -> 'Instance':
    url = secret.get("nbx_url")
    r = nbox_session.post(
      f"{url}/api/instance/create_new_instance_v4",
      json = {"project_name": name, "project_template": "blank"}
    )
    r.raise_for_status() # if its not 200, it's an error
    return cls(name, url)

  mv = None # atleast registered

  def refresh(self, id_or_name = None):
    id_or_name = id_or_name or self.instance_id
    if not isinstance(id_or_name, (int, str)):
      raise ValueError("Instance id must be an integer or a string")

    r = self.session.get(f"{self.url}/api/instance/get_user_instances")
    r.raise_for_status()
    resp = r.json()

    if len(resp["data"]) == 0:
      raise ValueError(f"No instance: '{id_or_name}' found, create manually from the dashboard or Instance.new(...)")

    key = "instance_id" if isinstance(id_or_name, int) else "name"
    instance = list(filter(lambda x: x[key] == id_or_name, resp["data"]))
    if len(instance) == 0:
      raise KeyError(id_or_name)
    instance = instance[0] # pick the first one

    for k,v in instance.items():
      if k in self.useful_keys:
        setattr(self, k, v)
    self.data = instance

    if self.state == "RUNNING":
      self.start()

  def start(self, cpu_only = True, cpu_count = 2, gpu = "p100", gpu_count = 1, region = "asia-south-1"):
    """``cpu_count`` should be one of [2, 4, 8]"""
    if self.__opened:
      logger.debug(f"Instance {self.name} ({self.instance_id}) is already opened")
      return

    if not self.state == "RUNNING":
      logger.debug(f"Starting instance {self.name} ({self.instance_id})")
      message = self.web_server.start_instance(
        "post",
        data = {
          "instance_id": self.instance_id,
          "hw":"cpu" if cpu_only else "gpu",
          "hw_config":{
            "cpu":f"n1-standard-{cpu_count}",
            "gpu":f"nvidia-tesla-{gpu}",
            "gpuCount": gpu_count,
          },
          "region": region,
        }
      )["msg"]
      if not message == "success":
        raise ValueError(message)

      logger.debug(f"Waiting for instance {self.name} ({self.instance_id}) to start")
      _i = 0
      while self.state != "RUNNING":
        time.sleep(5)
        self.refresh()
        _i += 1
        if _i > TIMEOUT_CALLS:
          raise TimeoutError("Instance did not start within timeout, please check dashboard")
      logger.debug(f"Instance {self.name} ({self.instance_id}) started")
    else:
      logger.debug(f"Instance {self.name} ({self.instance_id}) is already running")

    # now the instance is running, we can open it, opening will assign a bunch of cookies and
    # then get us the exact location of the instance
    logger.debug(f"Opening instance {self.name} ({self.instance_id})")
    self.open_data = self.web_server.open_instance(
      "post", data = {"instance_id":self.instance_id}
    )
    instance_url = self.open_data["base_url"].lstrip("/").rstrip("/")
    self.cs_url = f"{self.url}/{instance_url}"
    if self.cs_endpoint:
      self.cs_url += f"/{self.cs_endpoint}"

    # TODO:@yashbonde remove this check, once v2 on prod
    if self.cs_url.endswith("server"):
      self.__opened = True
      return

    # create a speced-subway, this requires capturing the openAPI spec first
    r = self.session.get(f"{self.cs_url}/openapi.json"); r.raise_for_status()
    self.cs_spec = r.json()
    self.compute_server = SpecSubway.from_openapi(self.cs_spec, self.cs_url, self.session)
    logger.debug(f"CS: {self.compute_server}")

    # now load all the functions from methods.py
    logger.debug(f"Testing instance {self.name} ({self.instance_id})")
    out = self.compute_server.test()
    with open(join(NBOX_HOME_DIR, "methods.py"), "w") as f:
      f.write(out["data"])

    sys.path.append(NBOX_HOME_DIR)
    from methods import __all__ as m_all
    for m in m_all:
      trg_name = f"bios_{m}"
      exec(f"from methods import {m} as {trg_name}")
      fn = partial(
        locals()[trg_name],
        cs_sub = self.compute_server,
        ws_sub = self.web_server,
        logger = logger
      )
      setattr(self, m, fn)

    # see if any developmental methods are saved locally
    try:
      from proto_methods import __all__ as p_all
      for m in p_all:
        trg_name = f"bios_{m}"
        exec(f"from proto_methods import {m} as {trg_name}")
        fn = partial(
          locals()[trg_name],
          cs_sub = self.compute_server,
          ws_sub = self.web_server,
          logger = logger
        )
        setattr(self, m, fn)
    except:
      pass

    self.__opened = True

  def stop(self):
    if self.state == "STOPPED":
      logger.debug(f"Instance {self.name} ({self.instance_id}) is already stopped")
      return

    logger.debug(f"Stopping instance {self.name} ({self.instance_id})")
    message = self.web_server.stop_instance("post", data = {"instance_id":self.instance_id})["msg"]
    if not message == "success":
      raise ValueError(message)

    logger.debug(f"Waiting for instance {self.name} ({self.instance_id}) to stop")
    _i = 0 # timeout call counter
    while self.state != "STOPPED":
      time.sleep(5)
      self.refresh()
      _i += 1
      if _i > TIMEOUT_CALLS:
        raise TimeoutError("Instance did not stop within timeout, please check dashboard")
    logger.debug(f"Instance {self.name} ({self.instance_id}) stopped")

    self.__opened = False

  def delete(self, force = False):
    if self.__opened and not force:
      raise ValueError("Instance is still opened, please call .stop() first")
    logger.debug(f"Deleting instance {self.name} ({self.instance_id})")
    message = self.web_server.delete_instance("post", data = {"instance_id":self.instance_id})["msg"]
    if not message == "success":
      raise ValueError(message)

  def __call__(self, x: str):
    """Caller is the most important UI/UX. The letter ``x`` in programming is reserved the most
    arbitrary thing, and this ``nbox.Instance`` is the gateway to a cloud instance. You can:
    1. run a script on the cloud
    2. run a local script on the cloud
    3. get the status of a script
    4. [TBD] run a special kind of functions known as
    `pure functions <https://en.wikipedia.org/wiki/Pure_function>`_

    Pure functions in programming are functions that are self sufficient in terms of execution,
    eg. all the packages are imported inside the function and there are no side effects in an
    execution (seeds included for probabilistic functions). Writing such functions in python with
    any IDE with checker is dead easy, however the performace guarantee is premium given high
    costs. Thus the logic to parsing these will have to be written as a seperate module.
    """
    if not self.__opened:
      raise ValueError("Instance is not opened, please call .start() first")
    if not isinstance(x, str):
      raise ValueError("x must be a string")

    def _run_cloud(fpath):
      fpath = "/mnt/disks/user/project/" + fpath.split("nbx://")[1]
      meta = list(filter(
        lambda y: y["path"] == fpath, self.compute_server.myfiles.info(fpath)["data"]
      ))
      if not meta:
        raise ValueError(f"File {x} not found on instance {self.name} ({self.instance_id})")
      return self.compute_server.rpc.start(fpath)["uid"]

    if re.match(r"[a-z]+-[a-z]+", x) is not None:
      logger.debug(f"Getting status of Job '{x}' on instance {self.name} ({self.instance_id})")
      data = self.compute_server.rpc.status(x)
      if not data["msg"] == "success":
        raise ValueError(data["msg"])
      else:
        status = data["status"]
        logger.debug(f"Script {x} on instance {self.name} ({self.instance_id}) is {status}")

      # if stopped then get the logs and run
      if status == "stopped":
        out = self.compute_server.rpc.logs(x)
        if out["err"]:
          logger.error("Execution errored out")
          return "error-done"
        else:
          return "done"
      return status
    elif os.path.isfile(x):
      self.mv(x, f"nbx://{x}")
      uid = _run_cloud(f"nbx://{x}")
      self.running_scripts.append(uid)
      return uid
    elif x.startswith("nbx://"):
      logger.debug("Running file on cloud")
      uid = _run_cloud(x)
      self.running_scripts.append(uid)
      return uid
    else:
      raise ValueError(f"Unknown: {x}")
