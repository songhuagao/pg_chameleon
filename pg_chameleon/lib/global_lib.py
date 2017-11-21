import rollbar
import pprint 
import yaml
import os, os.path
import sys
import time
import signal
from shutil import copy
from distutils.sysconfig import get_python_lib
from tabulate import tabulate
from pg_chameleon import pg_engine, mysql_source, pgsql_source
import logging
from logging.handlers  import TimedRotatingFileHandler
from daemonize import Daemonize
from multiprocessing import Process

class replica_engine(object):
	"""
		This class is wraps the the mysql and postgresql engines in order to perform the various activities required for the replica. 
		The constructor inits the global configuration class  and setup the mysql and postgresql engines as class objects. 
		The class sets the logging using the configuration parameter.
		
	"""
	def __init__(self, args):
		"""
			Class constructor.
		"""
		self.catalog_version = '2.0.0'
		self.upgradable_version = '1.6'
		self.lst_yes= ['yes',  'Yes', 'y', 'Y']
		python_lib=get_python_lib()
		cham_dir = "%s/.pg_chameleon" % os.path.expanduser('~')	
		
		
		local_conf = "%s/configuration/" % cham_dir 
		self.global_conf_example = '%s/pg_chameleon/configuration/config-example.yml' % python_lib
		self.local_conf_example = '%s/config-example.yml' % local_conf
		
		local_logs = "%s/logs/" % cham_dir 
		local_pid = "%s/pid/" % cham_dir 
		
		self.conf_dirs=[
			cham_dir, 
			local_conf, 
			local_logs, 
			local_pid, 
			
		]
		self.args = args
		self.set_configuration_files()
		self.args = args
		self.load_config()
		self.logger = self.__init_logger()
		
		#pg_engine instance initialisation
		self.pg_engine = pg_engine()
		self.pg_engine.dest_conn = self.config["pg_conn"]
		self.pg_engine.logger = self.logger
		self.pg_engine.source = self.args.source
		self.pg_engine.type_override = self.config["type_override"]
		self.pg_engine.sources = self.config["sources"]
		
		
		#mysql_source instance initialisation
		self.mysql_source = mysql_source()
		self.mysql_source.source = self.args.source
		self.mysql_source.tables = self.args.tables
		self.mysql_source.schema = self.args.schema.strip()
		self.mysql_source.pg_engine = self.pg_engine
		self.mysql_source.logger = self.logger
		self.mysql_source.sources = self.config["sources"]
		self.mysql_source.type_override = self.config["type_override"]
		
		#pgsql_source instance initialisation
		self.pgsql_source = pgsql_source()
		self.pgsql_source.source = self.args.source
		self.pgsql_source.tables = self.args.tables
		self.pgsql_source.schema = self.args.schema.strip()
		self.pgsql_source.pg_engine = self.pg_engine
		self.pgsql_source.logger = self.logger
		self.pgsql_source.sources = self.config["sources"]
		self.pgsql_source.type_override = self.config["type_override"]
		catalog_version = self.pg_engine.get_catalog_version()

		#safety checks
		if self.args.command == 'upgrade_replica_schema':
			self.pg_engine.sources = self.config["sources"]
			print("WARNING, entering upgrade mode. Disabling the catalogue version's check. Expected version %s, installed version %s" % (self.catalog_version, catalog_version))
		else:
			if  catalog_version:
				if self.catalog_version != catalog_version:
					print("FATAL, replica catalogue version mismatch. Expected %s, got %s" % (self.catalog_version, catalog_version))
					sys.exit()
			
		
		
		
	def terminate_replica(self, signal, frame):
		"""
			Stops gracefully the replica.
		"""
		self.logger.info("Caught stop replica signal terminating daemons and ending the replica process.")
		self.read_daemon.terminate()
		self.replay_daemon.terminate()
		self.pg_engine.connect_db()
		self.pg_engine.set_source_status("stopped")
		sys.exit(0)
		
	def set_configuration_files(self):
		""" 
			The method loops the list self.conf_dirs creating them only if they are missing.
			
			The method checks the freshness of the config-example.yaml and connection-example.yml 
			copies the new version from the python library determined in the class constructor with get_python_lib().
			
			If the configuration file is missing the method copies the file with a different message.
		
		"""

		for confdir in self.conf_dirs:
			if not os.path.isdir(confdir):
				print ("creating directory %s" % confdir)
				os.mkdir(confdir)
		
				
		if os.path.isfile(self.local_conf_example):
			if os.path.getctime(self.global_conf_example)>os.path.getctime(self.local_conf_example):
				print ("updating configuration example with %s" % self.local_conf_example)
				copy(self.global_conf_example, self.local_conf_example)
		else:
			print ("copying configuration  example in %s" % self.local_conf_example)
			copy(self.global_conf_example, self.local_conf_example)
	
	def load_config(self):
		""" 
			The method loads the configuration from the file specified in the args.config parameter.
		"""
		local_confdir = "%s/.pg_chameleon/configuration/" % os.path.expanduser('~')	
		self.config_file = '%s/%s.yml'%(local_confdir, self.args.config)
		
		if not os.path.isfile(self.config_file):
			print("**FATAL - configuration file missing. Please ensure the file %s is present." % (self.config_file))
			sys.exit()
		
		config_file = open(self.config_file, 'r')
		self.config = yaml.load(config_file.read())
		config_file.close()
		
	
	def show_sources(self):
		"""
			The method shows the sources available in the configuration file.
		"""
		for item in self.config["sources"]:
			print("\n")
			print (tabulate([], headers=["Source %s" % item]))
			tab_headers = ['Parameter', 'Value']
			tab_body = []
			source = self.config["sources"][item]
			config_list = [param for param in source if param not in ['db_conn']]
			connection_list = [param for param  in source["db_conn"] if param not in ['password']]
			for parameter in config_list:
				tab_row = [parameter, source[parameter]]
				tab_body.append(tab_row)
			for param in connection_list:
				tab_row = [param, source["db_conn"][param]]
				tab_body.append(tab_row)
		
			print(tabulate(tab_body, headers=tab_headers))
		
	def show_config(self):
		"""
			The method loads the current configuration and displays the status in tabular output
		"""
		config_list = [item for item in self.config if item not in ['pg_conn', 'sources', 'type_override']]
		connection_list = [item for item in self.config["pg_conn"] if item not in ['password']]
		type_override = pprint.pformat(self.config['type_override'], width = 20)
		tab_body = []
		tab_headers = ['Parameter', 'Value']
		for item in config_list:
			tab_row = [item, self.config[item]]
			tab_body.append(tab_row)
		for item in connection_list:
			tab_row = [item, self.config["pg_conn"][item]]
			tab_body.append(tab_row)
		tab_row = ['type_override', type_override]
		tab_body.append(tab_row)
		print(tabulate(tab_body, headers=tab_headers))
		self.show_sources()
		
	def create_replica_schema(self):
		"""
			The method creates the replica schema in the destination database.
		"""
		self.logger.info("Trying to create replica schema")
		self.pg_engine.create_replica_schema()
		
	def drop_replica_schema(self):
		"""
			The method removes the replica schema from the destination database.
		"""
		self.logger.info("Dropping the replica schema")
		self.pg_engine.drop_replica_schema()
	
	def add_source(self):
		"""
			The method adds a new replication source. A pre existence check is performed
		"""
		if self.args.source == "*":
			print("You must specify a source name with the argument --source")
		else:
			self.logger.info("Trying to add a new source")
			self.pg_engine.add_source()
			
	def drop_source(self):
		"""
			The method removes a replication source from the catalogue.
		"""
		if self.args.source == "*":
			print("You must specify a source name with the argument --source")
		else:
			drp_msg = 'Dropping the source %s will remove drop any replica reference.\n Are you sure? YES/No\n'  % self.args.source
			drop_src = input(drp_msg)
			if drop_src == 'YES':
				self.logger.info("Trying to remove the source")
				self.pg_engine.drop_source()
			elif drop_src in  self.lst_yes:
				print('Please type YES all uppercase to confirm')
	
	def init_replica(self):
		"""
			The method  initialise a replica for a given source and configuration. 
			It is compulsory to specify a source name when running this method.
			The method checks the source type and calls the corresponding initialisation's method.
			Currently only mysql is supported.
		"""
		if self.args.source == "*":
			print("You must specify a source name with the argument --source")
		elif self.args.tables != "*":
			print("You cannot specify a table name when running init_replica.")
		else:
			source_type = self.config["sources"][self.args.source]["type"]
			self.__stop_replica()
			if source_type  == "mysql":
				self.__init_mysql_replica()
			elif source_type  == "pgsql":
				self.__init_pgsql_replica()
			
	def __init_mysql_replica(self):
		"""
			The method  initialise a replica for a given mysql source within the specified configuration. 
			The method is called by the public method init_replica.
		"""
		
		if self.args.debug:
			self.mysql_source.init_replica()
		else:
			if self.config["log_dest"]  == 'stdout':
				foreground = True
			else:
				foreground = False
				print("Init replica process for source %s started." % (self.args.source))
			keep_fds = [self.logger_fds]
			init_pid = os.path.expanduser('%s/%s.pid' % (self.config["pid_dir"],self.args.source))
			self.logger.info("Initialising the replica for source %s" % self.args.source)
			init_daemon = Daemonize(app="init_replica", pid=init_pid, action=self.mysql_source.init_replica, foreground=foreground , keep_fds=keep_fds)
			init_daemon.start()

	def __init_pgsql_replica(self):
		"""
			The method  initialise a replica for a given postgresql source within the specified configuration. 
			The method is called by the public method init_replica.
		"""
		
		if self.args.debug:
			self.pgsql_source.init_replica()
		else:
			if self.config["log_dest"]  == 'stdout':
				foreground = True
			else:
				foreground = False
				print("Init replica process for source %s started." % (self.args.source))
			keep_fds = [self.logger_fds]
			init_pid = os.path.expanduser('%s/%s.pid' % (self.config["pid_dir"],self.args.source))
			self.logger.info("Initialising the replica for source %s" % self.args.source)
			init_daemon = Daemonize(app="init_replica", pid=init_pid, action=self.pgsql_source.init_replica, foreground=foreground , keep_fds=keep_fds)
			init_daemon.start()


	def refresh_schema(self):
		"""
			The method  reload the data from a source and only for a specified schema.
			Is compulsory to specify a source name and an origin's schema name. 
			The schema mappings are honoured by the procedure automatically.
		"""
		if self.args.source == "*":
			print("You must specify a source name using the argument --source")
		elif self.args.schema == "*":
			print("You must specify an origin's schema name using the argument --schema")
		else:
			self.__stop_replica()
			if self.args.debug:
				self.mysql_source.refresh_schema()
			else:
				if self.config["log_dest"]  == 'stdout':
					foreground = True
				else:
					foreground = False
					print("Sync tables process for source %s started." % (self.args.source))
				keep_fds = [self.logger_fds]
				init_pid = os.path.expanduser('%s/%s.pid' % (self.config["pid_dir"],self.args.source))
				self.logger.info("The tables %s within source %s will be synced." % (self.args.tables, self.args.source))
				sync_daemon = Daemonize(app="sync_tables", pid=init_pid, action=self.mysql_source.refresh_schema, foreground=foreground , keep_fds=keep_fds)
				sync_daemon .start()

				
	def sync_tables(self):
		"""
			The method  reload the data from a source only for specified tables.
			Is compulsory to specify a source name and at least one table name when running this method.
			Multiple tables are allowed if comma separated.
		"""
		if self.args.source == "*":
			print("You must specify a source name using the argument --source")
		elif self.args.tables == "*":
			print("You must specify one or more tables, in the form schema.table, separated by comma using the argument --tables")
		else:
			self.__stop_replica()
			if self.args.debug:
				self.mysql_source.sync_tables()
			else:
				if self.config["log_dest"]  == 'stdout':
					foreground = True
				else:
					foreground = False
					print("Sync tables process for source %s started." % (self.args.source))
				keep_fds = [self.logger_fds]
				init_pid = os.path.expanduser('%s/%s.pid' % (self.config["pid_dir"],self.args.source))
				self.logger.info("The tables %s within source %s will be synced." % (self.args.tables, self.args.source))
				sync_daemon = Daemonize(app="sync_tables", pid=init_pid, action=self.mysql_source.sync_tables, foreground=foreground , keep_fds=keep_fds)
				sync_daemon .start()
	
	def upgrade_replica_schema(self):
		"""
			The method upgrades an existing replica catalogue to the newer version.
			If the catalogue is from the previous version
		"""
		catalog_version = self.pg_engine.get_catalog_version()
		if catalog_version == self.catalog_version:
			print("The replica catalogue is already up to date.")
			sys.exit()
		else:
			if catalog_version == self.upgradable_version:
				upg_msg = 'Upgrading the catalogue %s to the version %s.\n Are you sure? YES/No\n'  %  (catalog_version, self.catalog_version)
				upg_cat = input(upg_msg)
				if upg_cat == 'YES':
					self.logger.info("Performing the upgrade")
					self.pg_engine.upgrade_catalogue_v1()
				elif upg_cat in  self.lst_yes:
					print('Please type YES all uppercase to confirm')
			elif catalog_version.split('.')[0] == '1':
				print('Wrong starting version. Expected %s, got %s') % (catalog_version, self.upgradable_version)
				sys.exit()
	
	def update_schema_mappings(self):
		"""
			The method updates the schema mappings for the given source. 
			The schema mappings is a configuration parameter but is stored in the replica
			catalogue when the source is added. If any change is made on the configuration file this method 
			should be called to update the system catalogue as well. The pg_engine method checks for any conflict before running
			the update on the tables t_sources and t_replica_tables.
			Is compulsory to specify a source name when running this method.
		"""
		if self.args.source == "*":
			print("You must specify a source name with the argument --source")
		else:
			self.__stop_replica()
			self.pg_engine.update_schema_mappings()
			
			
	def read_replica(self):
		"""
			The method reads the replica stream for the given source and stores the row images 
			in the target postgresql database.
		"""
		while True:
			self.mysql_source.read_replica()
			time.sleep(self.sleep_loop)
	
	def replay_replica(self):
		"""
			The method replays the row images stored in the target postgresql database.
		"""
		tables_error  = []
		self.pg_engine.connect_db()
		self.pg_engine.set_source_id()
		while True:
			tables_error = self.pg_engine.replay_replica()
			if len(tables_error) > 0:
				table_list = [item for sublist in tables_error for item in sublist]
				tables_removed = "\n".join(table_list)
				error_msg = "There was an error during the replay of data. %s. The affected tables are no longer replicated." % (tables_removed)
				self.logger.error(error_msg)
				if self.config["rollbar_key"] !='' and self.config["rollbar_env"] != '':
					rollbar.init(self.config["rollbar_key"], self.config["rollbar_env"])  
					rollbar.report_message(error_msg, 'error')
			time.sleep(self.sleep_loop)
			
	def run_replica(self):
		"""
			This method is the method which manages the two separate processes using the multiprocess library.
			It can be daemonised or run in foreground according with the --debug configuration or the log 
			destination.
		"""
		signal.signal(signal.SIGINT, self.terminate_replica)
		self.sleep_loop = self.config["sources"][self.args.source]["sleep_loop"]
		check_timeout = self.sleep_loop*10
		self.logger.info("Starting the replica daemons for source %s " % (self.args.source))
		self.read_daemon = Process(target=self.read_replica, name='read_replica')
		self.replay_daemon = Process(target=self.replay_replica, name='replay_replica')
		self.read_daemon.start()
		self.replay_daemon.start()
		while True:
			read_alive = self.read_daemon.is_alive()
			replay_alive = self.replay_daemon.is_alive()
			if  read_alive and replay_alive:
				self.logger.debug("Replica process for source %s is running" % (self.args.source))
			else:
				self.logger.error("Read process alive: %s - Replay process alive: %s" % (read_alive, replay_alive, ))
				
				if read_alive:
					self.read_daemon.terminate()
					self.logger.error("Replay daemon crashed. Terminating the read daemon.")
				if replay_alive:
					self.replay_daemon.terminate()
					self.logger.error("Read daemon crashed. Terminating the replay daemon.")
				self.pg_engine.connect_db()
				self.pg_engine.set_source_status("error")
				if self.config["rollbar_key"] !='' and self.config["rollbar_env"] != '':
					rollbar.init(self.config["rollbar_key"], self.config["rollbar_env"])  
					rollbar.report_message("The replica process crashed.\n Source: %s" %self.args.source, 'error')
					
				break
			time.sleep(check_timeout)
		self.logger.info("Replica process for source %s ended" % (self.args.source))
	
	def start_replica(self):
		"""
			The method starts a new replica process.
			Is compulsory to specify a source name when running this method.
		"""
		self.logger = self.__init_logger()
		replica_pid = os.path.expanduser('%s/%s.pid' % (self.config["pid_dir"],self.args.source))
				
		if self.args.source == "*":
			print("You must specify a source name using the argument --source")
		else:
			self.pg_engine.connect_db()
			self.logger.info("Cleaning not processed batches for source %s" % (self.args.source))
			self.pg_engine.clean_not_processed_batches()
			self.pg_engine.disconnect_db()
			if self.args.debug:
				self.run_replica()
			else:
				if self.config["log_dest"]  == 'stdout':
					foreground = True
				else:
					foreground = False
					print("Starting the replica process for source %s" % (self.args.source))
					keep_fds = [self.logger_fds]
					
					app_name = "%s_replica" % self.args.source
					replica_daemon = Daemonize(app=app_name, pid=replica_pid, action=self.run_replica, foreground=foreground , keep_fds=keep_fds)
					try:
						replica_daemon.start()
					except:
						print("The replica process is already started. Aborting the command.")
				
	
	def __stop_replica(self):
		"""
			The method reads the pid of the replica process for the given source and sends a SIGINT which 
			tells the replica process to manage a graceful exit.
		"""
		replica_pid = os.path.expanduser('%s/%s.pid' % (self.config["pid_dir"],self.args.source))
		if os.path.isfile(replica_pid):
			try:
				file_pid=open(replica_pid,'r')
				pid=file_pid.read()
				file_pid.close()
				os.kill(int(pid),2)
				print("Requesting the replica to stop")
				while True:
					try:
						os.kill(int(pid),0)
					except:
						break
				print("The replica process is stopped")
			except:
				print("An error occurred when trying to signal the replica process")
	
	def stop_replica(self):
		"""
			The method calls the private method __stop_replica to stop the replica process.
		"""
		self.__stop_replica()
	
	def show_errors(self):
		"""
			displays the error log entries if any.
			If the source the error log is filtered for this source only.
		"""
		log_id = self.args.logid
		self.pg_engine.source = self.args.source
		log_error_data = self.pg_engine.get_log_data(log_id)
		if log_error_data:
			if log_id != "*":
				tab_body = []
				log_line = log_error_data[0]
				tab_body.append(['Log id', log_line[0]])
				tab_body.append(['Source name', log_line[1]])
				tab_body.append(['ID Batch', log_line[2]])
				tab_body.append(['Table', log_line[3]])
				tab_body.append(['Schema', log_line[4]])
				tab_body.append(['Error timestamp', log_line[5]])
				tab_body.append(['SQL executed', log_line[6]])
				tab_body.append(['Error message', log_line[7]])
				print(tabulate(tab_body, tablefmt="simple"))
			else:
				tab_headers = ['Log id',  'Source name', 'ID Batch',  'Table', 'Schema' ,  'Error timestamp']
				tab_body = []
				for log_line in log_error_data:
					log_id = log_line[0]
					id_batch = log_line[1]
					source_name = log_line[2]
					table_name = log_line[3]
					schema_name = log_line[4]
					error_timestamp = log_line[5]
					tab_row = [log_id, id_batch,source_name, table_name,   schema_name, error_timestamp]
					tab_body.append(tab_row)
				print(tabulate(tab_body, headers=tab_headers, tablefmt="simple"))
		else:
			print('There are no errors in the log')
	def show_status(self):
		"""
			list the replica status from the replica catalogue.
			If the source is specified gives some extra details on the source status.
		"""
		self.pg_engine.source = self.args.source
		configuration_data = self.pg_engine.get_status()
		configuration_status = configuration_data[0]
		schema_mappings = configuration_data[1]
		table_status = configuration_data[2]
		tab_headers = ['Source id',  'Source name', 'Type',  'Status', 'Consistent' ,  'Read lag',  'Last read',  'Replay lag' , 'Last replay']
		tab_body = []
		for status in configuration_status:
			source_id = status[0]
			source_name = status[1]
			source_status = status[2]
			read_lag = status[3]
			last_read = status[4]
			replay_lag = status[5]
			last_replay = status[6]
			consistent = status[7]
			source_type = status[8]
			tab_row = [source_id, source_name, source_type,   source_status, consistent,  read_lag, last_read,  replay_lag, last_replay]
			tab_body.append(tab_row)
		print(tabulate(tab_body, headers=tab_headers, tablefmt="simple"))
		if schema_mappings:
			print('\n== Schema mappings ==')
			tab_headers = ['Origin schema',  'Destination schema']
			tab_body = []
			for mapping in schema_mappings:
				origin_schema =  mapping[0]
				destination_schema=  mapping[1]
				tab_row = [origin_schema, destination_schema]
				tab_body.append(tab_row)
			print(tabulate(tab_body, headers=tab_headers, tablefmt="simple"))
		if table_status:
			print('\n== Replica status ==')
			#tab_headers = ['',  '',  '']
			tab_body = []
			tables_no_replica = table_status[0]
			tab_row = ['Tables not replicated', tables_no_replica[1]]
			tab_body.append(tab_row)
			tables_with_replica = table_status[1]
			tab_row = ['Tables replicated', tables_with_replica[1]]
			tab_body.append(tab_row)
			tables_all= table_status[2]
			tab_row = ['All tables', tables_all[1]]
			tab_body.append(tab_row)
			print(tabulate(tab_body, tablefmt="simple"))
			if tables_no_replica[2]:
				print('\n== Tables with replica disabled ==')
				print("\n".join(tables_no_replica[2]))
	
	def detach_replica(self):
		"""
			The method terminates the replica process. The source is removed from the table t_sources with all the associated data.
			The schema sequences in are reset to the max values in the corresponding tables, leaving 
			the postgresql database as a standalone snapshot.
			The method creates the foreign keys existing in MySQL as well.
			Is compulsory to specify a source name when running this method.
		"""
		if self.args.source == "*":
			print("You must specify a source name with the argument --source")
		elif self.args.tables != "*":
			print("You cannot specify a table name when running init_replica.")
		else:
			drp_msg = 'Detaching the replica will remove any reference for the source %s.\n Are you sure? YES/No\n'  % self.args.source
			
			drop_src = input(drp_msg)
			if drop_src == 'YES':
				self.pg_engine.fk_metadata = self.mysql_source.get_foreign_keys_metadata()
				self.__stop_replica()
				self.pg_engine.detach_replica()
			elif drop_src in  self.lst_yes:
				print('Please type YES all uppercase to confirm')
				
		
			
	def __init_logger(self):
		"""
		The method initialise a new logger object using the configuration parameters.
		The formatter is different if the debug option is enabler or not.
		The method returns a new logger object and sets the logger's file descriptor in the class variable 
		logger_fds, used when the process is demonised.
		"""
		log_dir = self.config["log_dir"] 
		log_level = self.config["log_level"] 
		log_dest = self.config["log_dest"] 
		log_days_keep = self.config["log_days_keep"] 
		config_name = self.args.config
		source_name = self.args.source
		debug_mode = self.args.debug
		if source_name == '*':
			log_name = "%s_general" % (config_name)
		else:
			log_name = "%s_%s" % (config_name, source_name)
		
		log_file = os.path.expanduser('%s/%s.log' % (log_dir,log_name))
		logger = logging.getLogger(__name__)
		logger.setLevel(logging.DEBUG)
		logger.propagate = False
		if debug_mode:
			str_format = "%(asctime)s %(processName)s %(levelname)s %(filename)s (%(lineno)s): %(message)s"
		else:
			str_format = "%(asctime)s %(processName)s %(levelname)s: %(message)s"
		formatter = logging.Formatter(str_format, "%Y-%m-%d %H:%M:%S")
		
		if log_dest=='stdout' or debug_mode:
			fh=logging.StreamHandler(sys.stdout)
			
		elif log_dest=='file':
			fh = TimedRotatingFileHandler(log_file, when="d",interval=1,backupCount=log_days_keep)
		
		if log_level=='debug' or debug_mode:
			fh.setLevel(logging.DEBUG)
		elif log_level=='info':
			fh.setLevel(logging.INFO)
			
		fh.setFormatter(formatter)
		logger.addHandler(fh)
		self.logger_fds = fh.stream.fileno()
		return logger
