package com.camelot.sysaut.sensu.forwarder;

import com.amazonaws.client.builder.AwsClientBuilder;
import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import com.amazonaws.services.lambda.runtime.events.SQSEvent.SQSMessage;
import com.amazonaws.services.simplesystemsmanagement.AWSSimpleSystemsManagement;
import com.amazonaws.services.simplesystemsmanagement.AWSSimpleSystemsManagementClientBuilder;
import com.amazonaws.services.simplesystemsmanagement.model.GetParameterRequest;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Base64;
import java.util.Base64.Decoder;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 *
 * @author hoammcd
 */
public class Handler implements RequestHandler<SQSEvent, Void> {

  Gson gson = new GsonBuilder().setPrettyPrinting().create();
  Decoder decoder = Base64.getDecoder();

  // Netcool connection
  Connection connection = null;

  private String netcool_url = null;
  private String netcool_user = "root";
  private String netcool_password = null;
  private String ssm_netcool_url_path = null;
  private String ssm_netcool_password_path = null;
  private long last_ssm_update = 0;
  private final long update_period = 10 * 60 * 1000L; // 10 mins

//  public static SSMProvider ssmProvider = ParamManager.getSsmProvider(SsmClient.builder()
//          .httpClientBuilder(UrlConnectionHttpClient.builder())
//          // .credentialsProvider(EnvironmentVariableCredentialsProvider.create())
//          .build());
  public static AWSSimpleSystemsManagement ssmProvider = AWSSimpleSystemsManagementClientBuilder
          .standard()
          .build();

  private static Logger LOGGER = Logger.getLogger(Handler.class.getName());

  public Handler() {
  }

  public Handler(String url, String user, String password) {
    this.netcool_url = url;
    this.netcool_user = user;
    this.netcool_password = password;

  }

  @Override
  public Void handleRequest(SQSEvent event, Context context) {

    // If this is being tested via localstack, we will use environment variables for authentication
    if (System.getenv("LOCALSTACK_HOSTNAME") != null) {
      String endpoint = "http://" + System.getenv("LOCALSTACK_HOSTNAME") + ":" + System.getenv("EDGE_PORT");
      LOGGER.log(Level.INFO, "Pointing at custom endpoint {0}", endpoint);
//        ssmProvider = ParamManager.getSsmProvider(SsmClient.builder()
//                .httpClientBuilder(UrlConnectionHttpClient.builder())
//                .credentialsProvider(EnvironmentVariableCredentialsProvider.create())
//                .endpointOverride(new URI(endpoint))
//                .build());
//        
      ssmProvider = AWSSimpleSystemsManagementClientBuilder
              .standard()
              .withEndpointConfiguration(new AwsClientBuilder.EndpointConfiguration(endpoint, System.getenv("AWS_REGION")))
              .build();

    }

    LOGGER.info("Handling Queue message");

    for (SQSMessage message : event.getRecords()) {
      SensuAlert alert = gson.fromJson(message.getBody(), SensuAlert.class);

      // If the netcool host and password need populating, then get those from SSM
      if (netcool_url == null || netcool_password == null
              || System.currentTimeMillis() > last_ssm_update + update_period) {
        LOGGER.info("Getting creds from SSM");

        // Get the SSM paths from the ENV
        this.ssm_netcool_url_path = System.getenv("SSM_NETCOOL_URL_PATH");
        this.ssm_netcool_password_path = System.getenv("SSM_NETCOOL_URL_PASSWORD");

        LOGGER.log(Level.INFO, "Got SSM paths: {0} & {1}", new Object[]{this.ssm_netcool_url_path, this.ssm_netcool_password_path});

        // Get URL       
        GetParameterRequest parameterRequest = new GetParameterRequest();
        parameterRequest.withName(this.ssm_netcool_url_path).setWithDecryption(true);
        this.netcool_url = ssmProvider.getParameter(parameterRequest).getParameter().getValue();

        parameterRequest.withName(this.ssm_netcool_password_path).setWithDecryption(true);
        this.netcool_password = ssmProvider.getParameter(parameterRequest).getParameter().getValue();

//        this.netcool_url = ssmProvider.withDecryption().get(this.ssm_netcool_url_path);
//        this.netcool_password = ssmProvider.withDecryption().get(this.ssm_netcool_password_path);
        last_ssm_update = System.currentTimeMillis();
      }

      LOGGER.info(alert.toString());
      try {
        processEvent(alert);
      } catch (SQLException ex) {
        LOGGER.log(Level.SEVERE, null, ex);
      }

    }
    return null;
  }

  private void processEvent(SensuAlert alert) throws SQLException {
    this.getConnection();
    // Prepare a statement for inserting
    if (this.connection == null || this.connection.isClosed()) {
      LOGGER.severe("ERROR: Cannot get connection to ObjectServer");
    } else {

      int type = 1;
      if (alert.getSeverity() == 9) {
        type = 2;
      }

      String sql = String.format("insert into alerts.status "
              + "			( Identifier, Node, Agent, Manager, AlertGroup, AlertKey, Summary, Severity, FirstOccurrence, LastOccurrence, UserGroup, Type, ExpireTime, Environment ) "
              + "		values "
              + "			('%s %s', '%s', 'Sensu', 'OMNIBUS', 'Sensu-Lambda', '%s', '%s', %s, getdate, getdate, '%s', %s, %s, '%s' )",
               alert.getAlertKey(),
              alert.getSeverity(),
              alert.getNode(),
              alert.getAlertKey(),
              alert.getSummary(),
              alert.getSeverity(),
              alert.getTeam(),
              type,
              alert.getExpiry(),
              (alert.getEnvironment()) == null ? "" : alert.getEnvironment());

      Statement stmt = this.connection.createStatement();
      int rowsEffected = stmt.executeUpdate(sql);
      LOGGER.log(Level.INFO, "Inserted {0} rows", rowsEffected);
      stmt.close();

    }
  }

  // For local testing
  public static void main(String[] args) {
    Gson gson = new GsonBuilder().setPrettyPrinting().create();
    Decoder decoder = Base64.getDecoder();
    SensuAlert alert = gson.fromJson(new String(decoder.decode(args[0])), SensuAlert.class);

    Handler h = new Handler(args[1], args[2], args[3]);
    try {
      h.processEvent(alert);
    } catch (SQLException ex) {
      LOGGER.log(Level.SEVERE, null, ex);
    }

  }

  private void getConnection() throws SQLException {
    if (this.connection == null || this.connection.isClosed()) {
      Connection result = null;
      try {
        Class.forName("com.sybase.jdbc3.jdbc.SybDriver");
        result = DriverManager.getConnection(netcool_url, netcool_user, netcool_password);
      } catch (ClassNotFoundException ex) {
        LOGGER.log(Level.SEVERE, null, ex);
      }
      this.connection = result;
    }
  }

  private class SensuAlert {

    String summary;
    int severity;
    String alertKey;
    int expiry;
    String node;
    String team;
    String environment;

    public String getSummary() {
      return summary != null ? summary : "";
    }

    public void setSummary(String summary) {
      this.summary = summary;
    }

    public int getSeverity() {
      return severity;
    }

    public void setSeverity(int severity) {
      this.severity = severity;
    }

    public String getAlertKey() {
      return alertKey != null ? alertKey : "";
    }

    public void setAlertKey(String alertKey) {
      this.alertKey = alertKey;
    }

    public int getExpiry() {
      return expiry;
    }

    public void setExpiry(int expiry) {
      this.expiry = expiry;
    }

    public String getNode() {
      return node;
    }

    public void setNode(String node) {
      this.node = node;
    }

    public String getTeam() {
      return team != null ? team : "";
    }

    public void setTeam(String team) {
      this.team = team;
    }

    public String getEnvironment() {
      return environment != null ? environment : "";
    }

    public void setEnvironment(String environment) {
      this.environment = environment;
    }

    @Override
    public String toString() {
      return "SensuAlert{" + "summary=" + summary + ", severity=" + severity + ", alertKey=" + alertKey + ", expiry="
              + expiry + ", node=" + node + ", team=" + team + ", environment=" + environment + '}';
    }

  }
}
