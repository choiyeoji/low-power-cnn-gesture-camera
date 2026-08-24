module CNN_acc_controller #(
    parameter DATA_ADDR_WIDTH = 64 * 64 * 16
) (
    input logic clk,
    input logic rst_n,
    //conv_ctrl
    output logic conv_start,
    output logic conv_conv_en,
    output logic conv_Relu_en,
    output logic conv_MaxPool_en,
    input logic cnn_Done,
    //fc_ctrl
    output logic fc_start,
    output logic fc_finish_en,
    output logic [$clog2(1024)-1:0] fc_input_length,
    output logic [$clog2(64)-1:0] fc_output_length,
    input logic fc_Done,
    //padding_ctrl
    output logic [7:0] padding_size,  // 패딩 전 사이즈: 128/64/32
    output logic img_MUX_sel,
    output logic padding_en,
    //weight layer select
    output logic [2:0] layer,
    //Mem_ctrl
    output logic CONVFC_MUX_sel,
    output logic pingpong_W_sel,  //0 : A write 1 : B write 메모리로 선언
    //outside
    input logic cnn_start,
    output logic Done,
    output logic busy
);
    logic cnn_Done_reg;
    logic fc_Done_reg;
    logic cnn_Done_reg_d;
    logic fc_Done_reg_d;
    logic cnn_Done_clr;
    logic fc_Done_clr;

    typedef enum logic [3:0] {
        IDLE,
        CONV_1,
        CONV_2,
        CONV_3,
        MAXPOOL_1,
        MAXPOOL_2,
        FC_1,
        FC_2
    } state_t;

    state_t state, next_state;
    logic memory_owner_fc;

    assign CONVFC_MUX_sel = memory_owner_fc;

    always @(posedge clk) begin
        if (!rst_n) begin
            state <= IDLE;
            memory_owner_fc <= 1'b0;
        end else begin
            state <= next_state;
            memory_owner_fc <= (next_state == FC_1) || (next_state == FC_2);
        end
    end

    // done flag register: set by input done, clear by clr signal
    always @(posedge clk) begin
        if (!rst_n) begin
            cnn_Done_reg   <= 1'b0;
            fc_Done_reg    <= 1'b0;
            cnn_Done_reg_d <= 1'b0;
            fc_Done_reg_d  <= 1'b0;
        end else begin
            if (cnn_Done) cnn_Done_reg <= 1'b1;
            if (fc_Done) fc_Done_reg <= 1'b1;

            if (cnn_Done_clr) cnn_Done_reg <= 1'b0;
            if (fc_Done_clr) fc_Done_reg <= 1'b0;

            if (cnn_Done_clr) cnn_Done_reg_d <= 1'b0;
            else cnn_Done_reg_d <= cnn_Done_reg;

            if (fc_Done_clr) fc_Done_reg_d <= 1'b0;
            else fc_Done_reg_d <= fc_Done_reg;
        end
    end

    always_comb begin
        next_state       = state;

        // default outputs to avoid latch inference
        conv_start       = 0;
        conv_conv_en     = 0;
        conv_Relu_en     = 0;
        conv_MaxPool_en  = 0;
        fc_start         = 0;
        fc_finish_en     = 0;
        fc_input_length  = 0;
        fc_output_length = 0;
        padding_size     = 0;
        pingpong_W_sel   = 0;
        Done             = 0;
        layer            = 0;

        // busy is state-derived
        busy             = (state != IDLE);

        cnn_Done_clr     = 0;
        fc_Done_clr      = 0;

        //이미지 데이터 사용 MUX 스위칭
        if (state == CONV_1) img_MUX_sel = 1;  // img 사용
        else img_MUX_sel = 0;  //내부 메모리 사용
        if (state == CONV_1 || state == CONV_2 || state == CONV_3)
            padding_en = 1;
        else padding_en = 0;

        case (state)
            IDLE: begin
                //output hendsheck
                //done_reg
                cnn_Done_clr = 1;
                fc_Done_clr  = 1;
                if (cnn_start) begin
                    next_state                                    = CONV_1;
                    conv_start                                    = 1;
                    pingpong_W_sel                                = 0;
                    layer                                         = 1;
                    {conv_conv_en, conv_Relu_en, conv_MaxPool_en} = 3'b111;
                    padding_size                                  = 128;
                end
            end
            CONV_1: begin
                pingpong_W_sel = 0;
                layer          = 1;
                padding_size   = 128;
                if (cnn_Done_reg_d) begin
                    next_state                                    = CONV_2;
                    padding_size                                  = 64;
                    conv_start                                    = 1;
                    pingpong_W_sel                                = 1;
                    cnn_Done_clr                                  = 1;
                    {conv_conv_en, conv_Relu_en, conv_MaxPool_en} = 3'b111;
                    layer                                         = 2;
                end
            end
            CONV_2: begin
                pingpong_W_sel = 1;
                layer          = 2;
                padding_size   = 64;
                if (cnn_Done_reg_d) begin
                    padding_size                                  = 32;
                    next_state                                    = CONV_3;
                    conv_start                                    = 1;
                    pingpong_W_sel                                = 0;
                    cnn_Done_clr                                  = 1;
                    {conv_conv_en, conv_Relu_en, conv_MaxPool_en} = 3'b111;
                    layer                                         = 3;
                end
            end
            CONV_3: begin
                pingpong_W_sel = 0;
                layer          = 3;
                padding_size   = 32;
                if (cnn_Done_reg_d) begin
                    next_state                                    = MAXPOOL_1;
                    conv_start                                    = 1;
                    pingpong_W_sel                                = 1;
                    {conv_conv_en, conv_Relu_en, conv_MaxPool_en} = 3'b001;
                    cnn_Done_clr                                  = 1;
                    layer                                         = 0;
                end
            end
            MAXPOOL_1: begin
                pingpong_W_sel  = 1;
                layer           = 0;
                conv_MaxPool_en = 1;
                if (cnn_Done_reg_d) begin
                    next_state                                    = MAXPOOL_2;
                    conv_start                                    = 1;
                    pingpong_W_sel                                = 0;
                    {conv_conv_en, conv_Relu_en, conv_MaxPool_en} = 3'b001;
                    cnn_Done_clr                                  = 1;
                    layer                                         = 0;
                end
            end
            MAXPOOL_2: begin
                pingpong_W_sel = 0;
                layer          = 0;
                if (cnn_Done_reg_d) begin
                    next_state       = FC_1;
                    fc_start         = 1;
                    layer            = 3;
                    pingpong_W_sel   = 1;
                    cnn_Done_clr     = 1;
                    fc_input_length  = 1023;
                    fc_output_length = 63;
                    layer            = 4;
                end
            end
            FC_1: begin
                layer = 4;
                // FC_1은 MAXPOOL_2 결과가 있는 read bank를 유지해야 한다.
                pingpong_W_sel = 1;
                fc_input_length = 1023;
                fc_output_length = 63;
                if (fc_Done_reg_d) begin
                    next_state       = FC_2;
                    fc_start         = 1;
                    layer            = 4;
                    pingpong_W_sel   = 0;
                    fc_Done_clr      = 1;
                    fc_input_length  = 63;
                    fc_output_length = 0;
                    fc_finish_en     = 1;
                    layer            = 5;
                end
            end
            FC_2: begin
                pingpong_W_sel   = 0;
                layer            = 5;
                // FC_2는 FC_1 출력 bank를 읽도록 유지한다.
                pingpong_W_sel   = 0;
                fc_input_length  = 63;
                fc_output_length = 0;
                fc_finish_en     = 1;
                if (fc_Done_reg_d) begin
                    next_state  = IDLE;
                    fc_Done_clr = 1;
                    Done        = 1;
                end
            end
            default: next_state = IDLE;
        endcase

    end

endmodule
